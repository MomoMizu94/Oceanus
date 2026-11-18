""" Publish one locally generated image through the existing write permissions """
import hashlib
import json
from pathlib import Path

from agent_core.local_image_config import load_image_config, validate_image_request
from agent_core.local_image_runtime import generate_local_image
from agent_core.tools.reports import _export_report


def generate_image(prompt: str, negative_prompt: str, path: str, size: str, seed: int, *,
                   workspace_root: Path, active_model='', on_file_approval=None,
                   on_sensitive_approval=None, should_cancel=None, on_progress=None,
                   require_write_approval=False):
    validate_image_request(prompt, negative_prompt, size, seed)
    config = load_image_config()
    source = json.dumps({'prompt': prompt, 'negative_prompt': negative_prompt}, ensure_ascii=False)

    def prompt_snapshot(unused_path, **unused_options):
        return {'path': 'inline:image-generation-prompt', 'content': source,
                'sha256': hashlib.sha256(source.encode()).hexdigest()}

    def build(unused_source):
        return generate_local_image(
            config, prompt, negative_prompt, size, seed, active_model=active_model,
            should_cancel=should_cancel, on_progress=on_progress,
        )

    # Destination approval and collision checks happen before any generation request
    return _export_report(
        'inline:image-generation-prompt', path, extension='.png', media_type='image/png',
        source_limit=64000, build=build, read_snapshot=prompt_snapshot,
        workspace_root=workspace_root, on_file_approval=on_file_approval,
        on_sensitive_approval=on_sensitive_approval, should_cancel=should_cancel,
        require_write_approval=require_write_approval,
    )
