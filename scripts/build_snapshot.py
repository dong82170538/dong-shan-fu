"""Build a new stage4 snapshot, bound to actually tested code."""
import json
import os
import subprocess
import zipfile
from pathlib import Path
from snapshot_contract import ROOT, FORBIDDEN, code_manifest, collect_sources, digest, entries, manifest_sha

PREFIX='dong-shan-fu-independent-core'


def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()


def main():
    out=ROOT/os.environ.get('DSF_ARTIFACT_DIR','artifacts/stage4')
    binding=json.loads((out/'verification-binding.json').read_text(encoding='utf-8'))
    sources=collect_sources()
    code=code_manifest(sources)
    if binding['status']!='PASS' or code!=binding['tested_code_manifest']:
        raise RuntimeError('Current code is not the verified code; run verify_stage3.py first')
    for name,meta in binding['reports'].items():
        if digest((out/name).read_bytes())!=meta['sha256']:
            raise RuntimeError('Test report changed after verification')
    payload=dict(sources)
    for file in sorted(out.rglob('*')):
        relative=file.relative_to(ROOT)
        if file.is_symlink():
            raise RuntimeError('Evidence symlink is not allowed')
        if file.is_file() and file.suffix.lower() in {'.json','.txt','.sqlite3','.csv'} and file.name!='snapshot-receipt.json':
            payload[relative.as_posix()]=file.read_bytes()
    source_manifest=entries(sources)
    source_sha=manifest_sha(source_manifest)
    version={'kind':'independent_test_supervision_stage4','git_HEAD':git('rev-parse','HEAD'),'git_branch':git('branch','--show-current'),
             'git_index_tree':git('write-tree'),'git_status':git('status','--short','--branch'),
             'source_manifest':source_manifest,'source_manifest_sha256':source_sha,
             'tested_code_manifest_sha256':manifest_sha(code),'verification_binding':out.relative_to(ROOT).as_posix()+'/verification-binding.json',
             'original_source_included':False,'screenshots_included':False,'credentials_included':False,
             'runtime':'Python3.12+ standard library/SQLite; Node24.19+ for development; optional approved Electron44.9.3 not installed',
             'scope':'Executed Linux code fingerprint only; this ZIP has not been extracted and rerun. Electron/Windows/MV3 host remain unverified.'}
    payload['SNAPSHOT_VERSION.json']=(json.dumps(version,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    content=entries(payload)
    payload['CONTENTS_SHA256.json']=(json.dumps({'algorithm':'SHA256','members':content,'manifest_self_excluded':True},ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    folder=ROOT/'artifacts/snapshots';folder.mkdir(parents=True,exist_ok=True)
    archive=folder/('DongShanFu_stage4_'+source_sha[:12]+'.zip')
    if archive.exists():
        raise RuntimeError('Snapshot already exists; do not overwrite historical archives')
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        for name,data in sorted(payload.items()):
            member=zipfile.ZipInfo(PREFIX+'/'+name,date_time=(2026,10,25,0,0,0));member.external_attr=0o100644<<16;member.compress_type=zipfile.ZIP_DEFLATED
            output.writestr(member,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(archive) as output:
        if output.testzip() is not None:raise RuntimeError('Archive CRC failure')
        for name,meta in content.items():
            data=output.read(PREFIX+'/'+name)
            if digest(data)!=meta['sha256'] or len(data)!=meta['bytes']:raise RuntimeError('Archive content hash mismatch')
        for name in output.namelist():
            if any(part in FORBIDDEN for part in Path(name).parts) or name.lower().endswith(('.png','.jpg','.exe','.crx','.asar','.env','.pem','.key')):
                raise RuntimeError('Forbidden archive member')
    receipt={'archive':str(archive),'file_name':archive.name,'bytes':archive.stat().st_size,'sha256':digest(archive.read_bytes()),
             'source_manifest_sha256':source_sha,'tested_code_manifest_sha256':manifest_sha(code),
             'git_index_tree':version['git_index_tree'],'payload_files':len(payload),'source_files':len(sources),
             'integrity':'ZIP CRC and every payload SHA256 checked; no extracted ZIP rerun claimed',
             'transfer':'Library save authorized after verified stable archive; separate external receipt, no repository push',
             'previous_snapshot':{'library_file_id':'libfile_2b499b8980b481919f820fb077874fa5','bytes':770884,'sha256':'92cdcd4705b1ff1ac6e76ec783ee8c28f4671585d2986e6dfd3308264ade1792','tested_code_manifest_sha256':'c12b1dbb3644934c83b143c5bbb1355207d784ae5a6da78e4facda1f0ccaf1ea','git_index_tree':'dda2852bccba7cd0d3b79d71a4d3fdd9b0742164'}}
    (out/'snapshot-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':
    main()
