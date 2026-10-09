r"""Lossless, restartable EYES historical shard relocation. Never touches shard 01 or latest.

Usage:
  py -3.13 tools/archive_eye_shards.py --destination H:\BirdEye_Archive\EYES_20261009
  py -3.13 tools/archive_eye_shards.py --destination H:\BirdEye_Archive\EYES_20261009 --apply --limit 2
Restore: py -3.13 tools/archive_eye_shards.py --destination ... --restore <filename>
"""
import argparse, hashlib, json, os, sqlite3, sys, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "eye_Databa"
PREFIX = "eye_workspace_data_"
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda:handle.read(4*1024*1024),b""):
            h.update(data)
    return h.hexdigest()
def inspect_db(path):
    conn=sqlite3.connect("file:"+path.as_posix()+"?mode=ro",uri=True,timeout=5)
    try:
        outcome=conn.execute("PRAGMA quick_check").fetchone()[0]
        if outcome != "ok": raise RuntimeError(f"sqlite integrity failure: {path}: {outcome}")
        return {"file_count":conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
                "change_count":conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0]}
    finally:conn.close()
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination",required=True,type=Path)
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--verify",action="store_true",help="Read-only SHA-256 audit of all archived historical shards")
    parser.add_argument("--limit",type=int,default=0)
    parser.add_argument("--skip",action="append",default=[],help="Preserve an individual shard in place (e.g. corrupt SQLite source)")
    parser.add_argument("--restore",help="Restore exactly one verified historical shard to original location")
    options=parser.parse_args()
    out=options.destination.resolve()
    if out.drive.lower()==SOURCE.drive.lower():raise SystemExit("Archive must be on a different volume")
    out.mkdir(parents=True,exist_ok=True) if options.apply or options.restore else None
    if options.verify:
        entries=[]
        for meta in sorted(out.glob(PREFIX+"*.db.json")):
            m=json.loads(meta.read_text("utf-8"))
            z=out/(m["file"]+".zip")
            if not z.is_file() or digest(z)!=m["archive_sha256"]:
                raise RuntimeError("ARCHIVE_INTEGRITY_FAIL "+m["file"])
            with zipfile.ZipFile(z) as archive:
                with archive.open(m["file"]) as reader:
                    h=hashlib.sha256()
                    for part in iter(lambda:reader.read(4*1024*1024),b""):h.update(part)
                if h.hexdigest()!=m["source_sha256"]:
                    raise RuntimeError("ARCHIVED_SOURCE_HASH_MISMATCH "+m["file"])
            if (SOURCE/m["file"]).exists():
                raise RuntimeError("ALREADY_ARCHIVED_SOURCE_PRESENT "+m["file"])
            entries.append(m)
        quarantined=out/"quarantined_corrupt"/"eye_workspace_data_14.db.json"
        corrupt_record=None
        if quarantined.exists():
            corrupt_record=json.loads(quarantined.read_text("utf-8"))
            raw=Path(corrupt_record["destination"])
            if not raw.is_file() or digest(raw)!=corrupt_record["source_sha256"]:
                raise RuntimeError("CORRUPT_EVIDENCE_BACKUP_MISMATCH")
            if (SOURCE/corrupt_record["file"]).exists():
                raise RuntimeError("CORRUPT_SOURCE_STILL_PRESENT")
        result={"status":"PASS","healthy_archive_count":len(entries),
                "original_bytes":sum(m["source_bytes"] for m in entries),
                "archive_bytes":sum(m["archive_bytes"] for m in entries),
                "corrupt_raw_preserved":bool(corrupt_record),
                "corrupt_raw_bytes":corrupt_record["source_bytes"] if corrupt_record else 0,
                "remaining_shards":[p.name for p in sorted(SOURCE.glob(PREFIX+"*.db"))]}
        print(json.dumps(result,indent=2))
        return
    if options.restore:
        name=Path(options.restore).name
        assert name.startswith(PREFIX) and name.endswith(".db")
        quarantined=out/"quarantined_corrupt"/(name+".json")
        if quarantined.is_file():
            m=json.loads(quarantined.read_text("utf-8"))
            archived=Path(m["destination"])
            if not archived.is_file() or digest(archived)!=m["source_sha256"]:
                raise RuntimeError("QUARANTINE_SOURCE_HASH_MISMATCH")
            target=SOURCE/name
            if target.exists():raise RuntimeError("RESTORE_REFUSES_OVERWRITE")
            partial=target.with_suffix(".db.restore-partial")
            if partial.exists():raise RuntimeError("RESTORE_PARTIAL_EXISTS")
            try:
                import shutil
                shutil.copyfile(archived,partial)
                if digest(partial)!=m["source_sha256"]:
                    raise RuntimeError("RESTORE_CORRUPT_RAW_HASH_MISMATCH")
                os.replace(partial,target)
            finally:
                if partial.exists():partial.unlink()
            print(json.dumps({"status":"CORRUPT_ORIGINAL_RESTORED_AS_BYTES","file":str(target),
                              "sqlite_integrity":"CORRUPT_RETAINED"}))
            return
        z=out/(name+".zip")
        metadata=json.loads((out/(name+".json")).read_text("utf-8"))
        if digest(z)!=metadata["archive_sha256"]:raise SystemExit("ARCHIVE_HASH_MISMATCH")
        target=SOURCE/name
        if target.exists():raise SystemExit("RESTORE_REFUSES_OVERWRITE")
        tmp=target.with_suffix(".db.restore-partial")
        if tmp.exists():raise SystemExit("RESTORE_PARTIAL_EXISTS")
        try:
            with zipfile.ZipFile(z) as archive,archive.open(name) as incoming,tmp.open("xb") as writer:
                for chunk in iter(lambda:incoming.read(4*1024*1024),b""):writer.write(chunk)
            if digest(tmp)!=metadata["source_sha256"]:raise RuntimeError("RESTORE_SOURCE_HASH_MISMATCH")
            if inspect_db(tmp)!=metadata["sqlite_counts"]:raise RuntimeError("RESTORE_COUNTS_MISMATCH")
            os.replace(tmp,target)
            print(json.dumps({"status":"RESTORED","file":str(target)}))
        finally:
            if tmp.exists():tmp.unlink()
        return
    shards=sorted(SOURCE.glob(PREFIX+"*.db"))
    if not shards:raise SystemExit("NO_SHARDS")
    current=shards[-1]
    candidates=[p for p in shards if p!=shards[0] and p!=current and p.name not in options.skip]
    if options.limit>0:candidates=candidates[:options.limit]
    print(json.dumps({"mode":"apply" if options.apply else "dry_run","retained":[shards[0].name,current.name],"candidates":len(candidates),"bytes":sum(p.stat().st_size for p in candidates),"destination":str(out)}),flush=True)
    if not options.apply:return
    for p in candidates:
        if any((p.with_suffix(p.suffix+ext)).exists() and (p.with_suffix(p.suffix+ext)).stat().st_size for ext in ("-wal","-journal")):
            raise RuntimeError("NONEMPTY_SQLITE_JOURNAL: "+str(p))
        before=p.stat()
        record=inspect_db(p)
        original_hash=digest(p)
        archive=out/(p.name+".zip")
        meta=out/(p.name+".json")
        if archive.exists():
            if not meta.exists():raise RuntimeError("EXISTING_ARCHIVE_WITHOUT_METADATA: "+str(archive))
            existing=json.loads(meta.read_text("utf-8"))
            if existing["source_sha256"]!=original_hash or existing["source_bytes"]!=before.st_size or digest(archive)!=existing["archive_sha256"]:
                raise RuntimeError("PREEXISTING_ARCHIVE_MISMATCH "+str(archive))
        else:
            partial=archive.with_suffix(".zip.partial")
            if partial.exists():raise RuntimeError("PARTIAL_ARCHIVE_EXISTS: "+str(partial))
            with zipfile.ZipFile(partial,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=5,allowZip64=True) as z:
                z.write(p,arcname=p.name)
            with zipfile.ZipFile(partial) as z:
                if z.testzip() is not None:raise RuntimeError("ZIP_CRC_FAILED")
                with z.open(p.name) as reader:
                    hasher=hashlib.sha256()
                    for chunk in iter(lambda:reader.read(4*1024*1024),b""):hasher.update(chunk)
                if hasher.hexdigest()!=original_hash:raise RuntimeError("ZIP_PAYLOAD_HASH_MISMATCH")
            os.replace(partial,archive)
            existing={"file":p.name,"source_bytes":before.st_size,"source_sha256":original_hash,
                      "archive_bytes":archive.stat().st_size,"archive_sha256":digest(archive),
                      "sqlite_counts":record,"created_utc":datetime.now(timezone.utc).isoformat()}
            tmp=meta.with_suffix(".json.partial")
            tmp.write_text(json.dumps(existing,indent=2),encoding="utf-8")
            os.replace(tmp,meta)
        if p.stat().st_size!=before.st_size or p.stat().st_mtime_ns!=before.st_mtime_ns or digest(p)!=original_hash:
            raise RuntimeError("SOURCE_CHANGED_DURING_ARCHIVE: "+str(p))
        if record!=existing["sqlite_counts"]:raise RuntimeError("COUNT_MISMATCH")
        p.unlink()
        print(json.dumps({"status":"ARCHIVED_VERIFIED","file":p.name,"reclaimed_bytes":before.st_size,"archive_bytes":archive.stat().st_size,"archive":str(archive)}),flush=True)
if __name__=="__main__":main()
