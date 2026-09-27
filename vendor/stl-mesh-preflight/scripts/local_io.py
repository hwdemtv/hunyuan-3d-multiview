"""Project-owned bounded I/O helpers, extracted without freight business logic."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import unicodedata
LIMIT=4*1024*1024
PRODUCT_ID="stl-mesh-preflight"
VERSION="1.0.0"
class InputError(ValueError):
    pass

def need(ok, code):
    if not ok:
        raise InputError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(obj):
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')

def ident(v):
    need(type(v) is str and 0 < len(v) <= 100 and (v == v.strip()) and (not any((unicodedata.category(c) in {'Cc', 'Cs'} for c in v))), 'IDENTIFIER_INVALID')
    return v

def fields(obj, keys):
    need(type(obj) is dict and set(obj) == set(keys.split()), 'FIELDS_INVALID')

def number(v, positive=False, places=6, cap=1000000):
    need(type(v) is str and re.fullmatch('(?:0|[1-9][0-9]{0,9})(?:\\.[0-9]{1,' + str(places) + '})?', v) is not None, 'NUMBER_INVALID')
    result = Fraction(v)
    need(result <= cap and (not positive or result > 0), 'NUMBER_RANGE')
    return result

def no_links(path):
    for p in [path, *path.parents]:
        try:
            st = p.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(st.st_mode) and (not getattr(st, 'st_file_attributes', 0) & 1024), 'PATH_LINK_REJECTED')

def workspace(value):
    need(type(value) is str and (not value.startswith(('//', '\\\\'))) and (not any((unicodedata.category(c) in {'Cc', 'Cs'} for c in value))), 'WORKSPACE_INVALID')
    p = Path(value).absolute()
    no_links(p)
    need(p.is_dir() and p.parent != p, 'WORKSPACE_INVALID')
    return p.resolve(strict=True)

def local_path(root, value, output=False):
    need(type(value) is str and 0 < len(value) <= 240 and (value == value.strip()), 'PATH_INVALID')
    need(not any((unicodedata.category(c) in {'Cc', 'Cs'} for c in value)), 'PATH_INVALID')
    need(not value.startswith(('/', '\\')) and ':' not in value, 'PATH_INVALID')
    parts = value.replace('\\', '/').split('/')
    need(all((p and p not in {'.', '..'} and (not p.endswith(('.', ' '))) for p in parts)), 'PATH_INVALID')
    need(not any((re.fullmatch('(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\..*)?', p) for p in parts)), 'PATH_INVALID')
    p = root.joinpath(*parts)
    no_links(p)
    need(p.resolve(strict=not output).is_relative_to(root), 'PATH_OUTSIDE')
    if output:
        need(not p.exists() and p.parent.is_dir(), 'OUTPUT_EXISTS_OR_PARENT_MISSING')
    else:
        need(p.is_file(), 'INPUT_NOT_FILE')
    return p

def read_bytes(path, limit=LIMIT):

    def stamp(st):
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)
    no_links(path)
    before = path.lstat()
    need(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'FILE_SIZE_OR_TYPE')
    need(before.st_nlink == 1, 'FILE_HARDLINK_REJECTED')
    with path.open('rb') as f:
        opened = os.fstat(f.fileno())
        need(opened.st_nlink == 1, 'FILE_HARDLINK_REJECTED')
        need(stamp(opened) == stamp(before), 'INPUT_CHANGED')
        raw = f.read(limit + 1)
        finished = os.fstat(f.fileno())
        need(finished.st_nlink == 1, 'FILE_HARDLINK_REJECTED')
        need(stamp(finished) == stamp(before) and len(raw) == before.st_size, 'INPUT_CHANGED')
    no_links(path)
    after = path.lstat()
    need(after.st_nlink == 1, 'FILE_HARDLINK_REJECTED')
    need(len(raw) <= limit and stamp(before) == stamp(after), 'INPUT_CHANGED')
    return raw

def parse(raw):

    def pairs(items):
        obj = {}
        for key, value in items:
            need(key not in obj, 'DUPLICATE_JSON_KEY')
            obj[key] = value
        return obj

    def bad(_):
        raise InputError('JSON_NUMBER_UNSUPPORTED')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=bad, parse_float=bad)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError('JSON_INVALID') from exc

def recheck_inputs(captures):
    for value in captures.values():
        need(read_bytes(value['path'], value['limit']) == value['raw'], 'INPUT_CHANGED')

def deliver(root, out, report, files, captures):
    no_links(out)
    need(not out.exists() and out.parent.is_dir() and out.is_relative_to(root), 'OUTPUT_INVALID')
    out.mkdir()
    for name, raw in files.items():
        no_links(out / name)
        with (out / name).open('xb') as f:
            f.write(raw)
    for name, raw in files.items():
        need(read_bytes(out / name, 16 * 1024 * 1024) == raw, 'OUTPUT_READBACK_FAILED')
    need(parse(read_bytes(out / 'audit.json', 16 * 1024 * 1024)) == report, 'REPORT_READBACK_FAILED')
    recheck_inputs(captures)
    need(report['inputs'] == {k: {'sha256': sha(v['raw']), 'bytes': len(v['raw'])} for k, v in captures.items()}, 'INPUT_MANIFEST_MISMATCH')
    manifest = {'complete': True, 'original_id': PRODUCT_ID, 'version': VERSION, 'mode': 'FREE_LOCAL_COMPLETE', 'run_id': report['run_id'], 'inputs': report['inputs'], 'output_sha256': {k: sha(v) for k, v in files.items()}}
    raw = canonical(manifest)
    marker = out / 'run-manifest.json'
    marker_created, identity = (False, None)
    try:
        no_links(marker)
        with marker.open('xb') as f:
            marker_created = True
            st = os.fstat(f.fileno())
            identity = (st.st_dev, st.st_ino)
            f.write(raw)
        need(read_bytes(marker) == raw and parse(read_bytes(marker)) == manifest, 'MANIFEST_READBACK_FAILED')
        for name,expected in files.items():
            need(read_bytes(out/name,16*1024*1024)==expected,'OUTPUT_CHANGED_BEFORE_COMPLETE')
        recheck_inputs(captures)
    except (OSError, InputError):
        if marker_created:
            try:
                no_links(marker)
                st = marker.stat()
                if (st.st_dev, st.st_ino) == identity:
                    marker.unlink()
            except (OSError, InputError):
                pass
        raise
    return manifest
