#!/usr/bin/env python3
"""Deterministic PhotoCraft-protocol fixture; never a substitute for its renderer."""
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
read_root = Path(args[args.index('--automation-read-root') + 1]) if '--automation-read-root' in args else None
write_root = Path(args[args.index('--automation-write-root') + 1]) if '--automation-write-root' in args else None
names = ['command_list', 'doc_open', 'doc_new', 'command_run', 'doc_save', 'doc_inspect', 'render_preview']
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request:
        continue
    method = request['method']
    if method == 'initialize':
        result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture'}}
    elif method == 'tools/list':
        result = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}} for name in names]}
    elif method == 'tools/call':
        name = request['params']['name']
        params = request['params']['arguments']
        error = False
        text = name
        if name == 'doc_open':
            assert read_root is not None and (read_root / params['path']).is_file()
            assert params['path'] == Path(params['path']).name
        if name == 'command_run':
            assert params['wait'] is True
            if params['id'] == 'filter.fail':
                error, text = True, 'requested failure'
        if name == 'doc_save':
            assert write_root is not None
            assert params['path'] == Path(params['path']).name
            (write_root / params['path']).write_bytes(b'fixture-output')
        if name == 'doc_inspect':
            text = json.dumps({'pid': os.getpid(), 'read_root': str(read_root), 'write_root': str(write_root)})
        content = [{'type': 'text', 'text': text}]
        if name == 'render_preview':
            assert params['max_side'] == 1024
            content += [{'type': 'image', 'data': 'aGVsbG8=', 'mimeType': 'image/png'}]
        result = {'content': content, 'isError': error}
    else:
        result = {}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
