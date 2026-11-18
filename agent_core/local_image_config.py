""" User-owned configuration and fixed local image workflows """
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


MAX_PROMPT_CHARS = 4000
SIZES = {'square': (1024, 1024), 'landscape': (1344, 768), 'portrait': (768, 1344)}
SERVICE_DEFAULTS = {
    'comfy_url': 'http://127.0.0.1:8189',
    'timeout_seconds': 600,
    'unload_ollama': True,
}
DEFAULT_PROFILE = 'z-image-base'
IMAGE_PROFILES = {
    'z-image-base': {
        'name': 'Z-Image Base',
        'models': {
            'diffusion_model': 'z_image_bf16.safetensors',
            'text_encoder': 'qwen_3_4b.safetensors',
            'vae': 'ae.safetensors',
        },
        'sampling': {'steps': 28, 'cfg': 4.0, 'sampler': 'res_multistep', 'scheduler': 'simple', 'shift': 3.0},
    },
}
DEFAULTS = SERVICE_DEFAULTS | {'profile': DEFAULT_PROFILE} | IMAGE_PROFILES[DEFAULT_PROFILE]['models']


def get_image_profile(name):
    if not isinstance(name, str) or name not in IMAGE_PROFILES:
        raise ValueError('Unknown image profile. Choose ' + ', '.join(IMAGE_PROFILES) + '.')
    return IMAGE_PROFILES[name]


def local_endpoint(value):
    if not isinstance(value, str) or any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError('Image services require a local HTTP address.')
    parts = urlsplit(value)
    if (parts.scheme != 'http' or parts.hostname not in ('localhost', '127.0.0.1', '::1')
            or parts.username is not None or parts.password is not None
            or parts.path not in ('', '/') or parts.query or parts.fragment):
        raise ValueError('Image services must use a loopback HTTP address without credentials or a path.')
    port = parts.port if parts.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError('Invalid local service port.')
    host = '127.0.0.1' if parts.hostname == 'localhost' else parts.hostname
    return host, port


def load_image_config():
    location = Path.home() / '.oceanus/image-generation.json'
    try:
        descriptor = os.open(location, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ValueError('Local image generation is not configured. Set up ~/.oceanus/image-generation.json using docs/local-image-generation.md.') from None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Image configuration must be a regular file.')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            raw = source.read(8193)
    finally:
        os.close(descriptor)
    if len(raw) > 8192:
        raise ValueError('Image configuration exceeds 8 KiB.')
    return resolve_image_config(json.loads(raw))


def resolve_image_config(options):
    if not isinstance(options, dict):
        raise ValueError('Image configuration must be an object.')
    profile_name = options.get('profile', DEFAULT_PROFILE)
    profile = get_image_profile(profile_name)
    defaults = SERVICE_DEFAULTS | {'profile': profile_name} | profile['models']
    if set(options) - set(defaults):
        raise ValueError('Unknown or incompatible image configuration options. Remove fields from the previous profile.')
    config = defaults | options
    local_endpoint(config['comfy_url'])
    for field in profile['models']:
        value = config[field]
        if (not isinstance(value, str) or len(value) > 200
                or re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*\.safetensors', value) is None
                or '..' in value):
            raise ValueError('Configure local .safetensors basenames, not paths or URLs.')
    if type(config['timeout_seconds']) is not int or not 30 <= config['timeout_seconds'] <= 1800:
        raise ValueError('Image timeout must be an integer from 30 to 1800 seconds.')
    if type(config['unload_ollama']) is not bool:
        raise ValueError('unload_ollama must be true or false.')
    return config


def validate_image_request(prompt, negative_prompt, size, seed):
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError('Image prompt must contain 1..4000 characters.')
    if any(ord(character) < 32 and character not in '\n\t' for character in prompt):
        raise ValueError('Image prompt contains unsupported control characters.')
    if not isinstance(negative_prompt, str) or len(negative_prompt) > MAX_PROMPT_CHARS:
        raise ValueError('Negative prompt must be text of at most 4000 characters.')
    if any(ord(character) < 32 and character not in '\n\t' for character in negative_prompt):
        raise ValueError('Negative prompt contains unsupported control characters.')
    if not isinstance(size, str) or size not in SIZES:
        raise ValueError('Choose square, landscape, or portrait.')
    if type(seed) is not int or not 0 <= seed <= 2**53 - 1:
        raise ValueError('Image seed must be an integer from 0 to 9007199254740991.')


def build_image_workflow(config, prompt, negative_prompt, size, seed, prefix):
    validate_image_request(prompt, negative_prompt, size, seed)
    config = resolve_image_config(config)
    profile = get_image_profile(config['profile'])
    settings = profile['sampling']
    width, height = SIZES[size]
    return {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': config['diffusion_model'], 'weight_dtype': 'default'}},
        '2': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': config['text_encoder'], 'type': 'lumina2', 'device': 'default'}},
        '3': {'class_type': 'VAELoader', 'inputs': {'vae_name': config['vae']}},
        '4': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': prompt}},
        '5': {'class_type': 'CLIPTextEncode', 'inputs': {'clip': ['2', 0], 'text': negative_prompt}},
        '6': {'class_type': 'ModelSamplingAuraFlow', 'inputs': {'model': ['1', 0], 'shift': settings['shift']}},
        '7': {'class_type': 'EmptySD3LatentImage', 'inputs': {'width': width, 'height': height, 'batch_size': 1}},
        '11': {'class_type': 'KSampler', 'inputs': {
            'model': ['6', 0], 'positive': ['4', 0], 'negative': ['5', 0],
            'latent_image': ['7', 0], 'seed': seed, 'steps': settings['steps'], 'cfg': settings['cfg'],
            'sampler_name': settings['sampler'], 'scheduler': settings['scheduler'], 'denoise': 1.0,
        }},
        '12': {'class_type': 'VAEDecode', 'inputs': {'samples': ['11', 0], 'vae': ['3', 0]}},
        '13': {'class_type': 'SaveImage', 'inputs': {'images': ['12', 0], 'filename_prefix': prefix}},
    }
