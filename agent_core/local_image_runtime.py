""" Run one local ComfyUI job and release its GPU allocations before returning """
from contextlib import contextmanager
import fcntl
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import urlencode
import uuid

from PIL import Image, PngImagePlugin

from agent_core.image_attachments import MAX_IMAGE_BYTES, validate_image_bytes
from agent_core.local_image_config import (
    build_image_workflow, get_image_profile, local_endpoint, resolve_image_config, SIZES,
)
from agent_core.local_image_http import LocalImageClient, check_image_job


class ImageCleanupError(OSError):
    """ Stop the task when image memory or execution could not be cleared """


@contextmanager
def image_job_lock(endpoint):
    directory = Path.home() / '.local/share/oceanus/image-generation'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('Image-generation state directory must be private to your user.')
    endpoint_key = json.dumps(local_endpoint(endpoint)).encode()
    name = hashlib.sha256(endpoint_key).hexdigest() + '.lock'
    descriptor = os.open(directory / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError('Invalid image-generation lock file.')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OSError('Another Oceanus image job is active. Wait for it to finish.') from None
        yield
    finally:
        os.close(descriptor)


def queue_ids(client):
    queue = client.json('GET', '/queue')
    ids = set()
    for field in ('queue_running', 'queue_pending'):
        entries = queue.get(field)
        if not isinstance(entries, list):
            raise ValueError('Invalid ComfyUI queue response.')
        for entry in entries:
            if not isinstance(entry, list) or len(entry) < 2 or not isinstance(entry[1], str):
                raise ValueError('Invalid ComfyUI queue entry.')
            ids.add(entry[1])
    return ids


def preflight_workflow(client, workflow, job_id):
    if queue_ids(client):
        raise OSError('ComfyUI is busy. Use the dedicated Oceanus instance and retry when idle.')
    # Probe targeted cancellation before submitting work to an older server
    probe = client.json('POST', f'/api/jobs/{job_id}/cancel', {})
    if probe.get('cancelled') is not False:
        raise ValueError('ComfyUI must support targeted job cancellation. Update ComfyUI.')
    classes = sorted({node['class_type'] for node in workflow.values()})
    definitions = {}
    for name in classes:
        result = client.json('GET', '/object_info/' + name)
        if not isinstance(result.get(name), dict):
            raise ValueError(f'ComfyUI is missing the core {name} node. Update ComfyUI.')
        definitions[name] = result[name]
    model_fields = {
        'UNETLoader': 'unet_name', 'CLIPLoader': 'clip_name',
        'VAELoader': 'vae_name', 'CheckpointLoaderSimple': 'ckpt_name',
    }
    for node in workflow.values():
        field = model_fields.get(node['class_type'])
        if field is None:
            continue
        try:
            choices = definitions[node['class_type']]['input']['required'][field][0]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError('ComfyUI returned invalid model choices.') from error
        if not isinstance(choices, list) or node['inputs'][field] not in choices:
            raise ValueError(f'Install the configured {field} file in ComfyUI before generating images.')


def unload_active_ollama(model, deadline, should_cancel):
    if not model.startswith(('ollama_chat/', 'ollama/')):
        return False
    tag = model.split('/', 1)[1]
    endpoint = (os.environ.get('OLLAMA_API_BASE') or 'http://127.0.0.1:11434').rstrip('/')
    endpoint = endpoint.removesuffix('/api/chat')
    client = LocalImageClient(endpoint, deadline, should_cancel)
    response = client.json('POST', '/api/generate', {'model': tag, 'keep_alive': 0, 'stream': False})
    if response.get('done') is not True:
        raise OSError('Ollama did not confirm the model unload request.')
    while True:
        response = client.json('GET', '/api/ps')
        models = response.get('models')
        if not isinstance(models, list):
            raise ValueError('Invalid Ollama running-model response.')
        still_loaded = False
        for loaded in models:
            if not isinstance(loaded, dict):
                raise ValueError('Invalid Ollama running-model entry.')
            name = loaded.get('name') or loaded.get('model')
            if name in (tag, tag + ':latest'):
                still_loaded = True
        if not still_loaded:
            return True
        time.sleep(0.2)


def collect_job_image(client, job_id, prefix):
    while True:
        reply = client.json('GET', '/history/' + job_id)
        history = reply.get(job_id)
        if history is not None:
            if not isinstance(history, dict) or not isinstance(history.get('status'), dict):
                raise ValueError('Invalid ComfyUI job history.')
            status = history['status']
            if status.get('status_str') == 'error':
                raise OSError('ComfyUI image generation failed. Inspect the local ComfyUI log.')
            if status.get('completed') is True:
                if status.get('status_str') != 'success':
                    raise OSError('ComfyUI did not report successful generation.')
                try:
                    images = history['outputs']['13']['images']
                except (KeyError, TypeError) as error:
                    raise ValueError('ComfyUI returned no image for the requested output node.') from error
                if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
                    raise ValueError('Expected exactly one generated image.')
                image = images[0]
                filename = image.get('filename')
                if (not isinstance(filename, str) or len(filename) > 200
                        or re.fullmatch(re.escape(prefix) + r'_[0-9]+_?\.png', filename) is None
                        or image.get('subfolder') != '' or image.get('type') != 'output'):
                    raise ValueError('ComfyUI returned an unexpected image location.')
                query = urlencode({'filename': filename, 'subfolder': '', 'type': 'output'})
                return client.request('GET', '/view?' + query, max_bytes=MAX_IMAGE_BYTES)
        check_image_job(client.deadline, client.should_cancel)
        time.sleep(0.2)


def cleanup_job(endpoint, job_id):
    # Cleanup has its own short deadline and must run even after cancellation
    client = LocalImageClient(endpoint, time.monotonic() + 30)
    client.json('POST', f'/api/jobs/{job_id}/cancel', {})
    while job_id in queue_ids(client):
        time.sleep(0.2)
    if queue_ids(client):
        raise OSError('Another ComfyUI job is active; its model memory was not unloaded.')
    client.request('POST', '/free', {'unload_models': True, 'free_memory': True})
    while True:
        if queue_ids(client):
            raise OSError('ComfyUI received another job during cleanup. Use a dedicated instance.')
        stats = client.json('GET', '/system_stats')
        devices = stats.get('devices')
        if not isinstance(devices, list) or not devices:
            raise ValueError('Cannot verify ComfyUI GPU memory release.')
        released = True
        for device in devices:
            if not isinstance(device, dict):
                raise ValueError('Invalid ComfyUI device information.')
            if device.get('type') == 'cpu':
                continue
            reserved = device.get('torch_vram_total')
            if type(reserved) is not int or reserved < 0:
                raise ValueError('Cannot verify ComfyUI GPU memory release.')
            if reserved > 256 * 1024 * 1024:
                released = False
        if released:
            return
        time.sleep(0.2)


def encode_generated_png(raw, metadata, size):
    properties = validate_image_bytes(raw)
    if (properties['width'], properties['height']) != SIZES[size]:
        raise ValueError('Generated image dimensions do not match the request.')
    info = PngImagePlugin.PngInfo()
    info.add_text('Oceanus', json.dumps(metadata, ensure_ascii=False))
    output = BytesIO()
    with Image.open(BytesIO(raw)) as image:
        image.convert('RGB').save(output, format='PNG', pnginfo=info)
    png = output.getvalue()
    validate_image_bytes(png)
    return png


def generate_local_image(config, prompt, negative_prompt, size, seed, *, active_model='',
                         should_cancel=None, on_progress=None):
    config = resolve_image_config(config)
    profile = get_image_profile(config['profile'])
    start = time.monotonic()
    deadline = start + config['timeout_seconds']
    job_id = str(uuid.uuid4())
    prefix = 'oceanus-' + job_id
    workflow = build_image_workflow(config, prompt, negative_prompt, size, seed, prefix)
    workflow_hash = hashlib.sha256(json.dumps(workflow, sort_keys=True).encode()).hexdigest()
    client = LocalImageClient(config['comfy_url'], deadline, should_cancel)
    with image_job_lock(config['comfy_url']):
        preflight_workflow(client, workflow, job_id)
        unloaded = False
        if config['unload_ollama']:
            if on_progress is not None:
                on_progress('Preparing GPU memory for local image generation.')
            unloaded = unload_active_ollama(active_model, deadline, should_cancel)
        if queue_ids(client):
            raise OSError('ComfyUI became busy before submission. No image job was submitted.')
        try:
            if on_progress is not None:
                on_progress('Generating an image locally with ' + profile['name'] + '.')
            # A known ID lets cleanup find our job even if the submission response is lost
            response = client.json('POST', '/prompt', {'prompt': workflow, 'prompt_id': job_id, 'client_id': job_id})
            if response.get('prompt_id') != job_id or response.get('node_errors'):
                raise ValueError('ComfyUI did not accept the requested job ID and workflow. Update ComfyUI.')
            raw = collect_job_image(client, job_id, prefix)
        finally:
            try:
                if on_progress is not None:
                    on_progress('Releasing the local image model from GPU memory.')
            finally:
                # A failed display callback must not leave the image model loaded
                try:
                    cleanup_job(config['comfy_url'], job_id)
                except (OSError, ValueError) as error:
                    raise ImageCleanupError(
                        f'Image cleanup could not be confirmed: {error} '
                        'No workspace image was published. Stop the dedicated ComfyUI service before continuing.'
                    ) from error
        check_image_job(deadline, should_cancel)
        metadata = {
            'generator': 'local-comfyui', 'profile': config['profile'], 'model_family': profile['name'],
            'prompt': prompt, 'negative_prompt': negative_prompt,
            'seed': seed, 'width': SIZES[size][0], 'height': SIZES[size][1],
            **profile['sampling'],
            'job_id': job_id, 'workflow_sha256': workflow_hash,
            'ollama_unloaded': unloaded, 'comfy_memory_released': True,
            'note': 'Generated illustration, not evidence or a calculated chart. Model filenames do not verify weight contents.',
        }
        for field in profile['models']:
            metadata[field] = config[field]
        png = encode_generated_png(raw, metadata, size)
        check_image_job(deadline, should_cancel)
        metadata['image_sha256'] = hashlib.sha256(png).hexdigest()
        metadata['elapsed_seconds'] = round(time.monotonic() - start, 2)
        return png, metadata
