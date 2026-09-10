#!/usr/bin/env python3
"""Report resource usage for this Compose project only, without secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess

root = Path(__file__).resolve().parent
docker = ['docker', '--config', str(root / 'docker-client')]

def run(args):
    return subprocess.check_output(args, text=True).strip()

def allocated_kib(directory):
    # SQLite journals and other live files may disappear during the sample.
    # Count allocated blocks once per inode without following directory symlinks.
    seen, blocks = set(), 0
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for item in [Path(parent)] + [Path(parent) / name for name in files]:
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            identity = info.st_dev, info.st_ino
            if identity not in seen:
                seen.add(identity)
                blocks += info.st_blocks
    return (blocks + 1) // 2

ids = run(docker + ['ps', '-q', '--filter', 'label=com.docker.compose.project=weixin']).splitlines()
report = {'running_containers': len(ids)}
if ids:
    report['containers'] = [json.loads(line) for line in run(docker + [
        'stats', '--no-stream', '--format', '{{json .}}', *ids
    ]).splitlines()]
    image_ids = sorted({run(docker + ['inspect', '--format', '{{.Image}}', ident]) for ident in ids})
    report['images'] = [json.loads(run(docker + ['image', 'inspect', '--format',
        '{"id":"{{.Id}}","reported_image_bytes":{{.Size}}}', ident])) for ident in image_ids]
disk = shutil.disk_usage(root)
report['filesystem_free_GiB'] = round(disk.free / (1024 ** 3), 2)
report['project_disk_KiB'] = allocated_kib(root)
report['host_memory'] = run(['free', '-m'])
print(json.dumps(report, ensure_ascii=False, indent=2))
