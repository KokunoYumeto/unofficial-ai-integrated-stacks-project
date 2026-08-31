"""R39 source-only materialization. Never runs builds or admits an overlay."""
from pathlib import Path
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[4]
WORKSPACE = REPO.parent
LANE = WORKSPACE / "03_projects/language_management/romance/00_lane_control"
PRODUCER = WORKSPACE / "03_projects/language_management/romance/03_working_translations/stacks_fr_20260821/p03/evidence"
UPSTREAM = WORKSPACE / "03_projects/language_management/cjk/03_working_translations/stacks_cjk_20260821/upstream/src/stacks-project-a04446e57ec1fbc252a871afcec7752fb2807b14"
SOURCE = "sites-cohomology.tex"
AUTH_SHA = "5B335CE2C7208A128B3C744B8828D93508063F41A4FABBCE2E921F890928895C"
PAYLOAD_SHA = "002642AD67BC27A1A0CACDF44EB548AD5997AEBA07007E22D8B130E98A2F4ABD"
COMMIT = "a04446e57ec1fbc252a871afcec7752fb2807b14"
TREE = "3feeb703b931a6e7259782c10e7d1575adc83e5e"
HEAD = "69f14d67c3a456c3d1447e1a201bdfc3f3d87f0c"
CID = "stacks-errata-a04446e-r39"
LEASE = "stacks-lease-000043-errata-r39"
STAMP = "2026-08-31T13:10:07Z"
EXPECTED = {
 "SITES_COHOMOLOGY_R39_DEDUP_AND_PLAN_20260831.json": (LANE,63982,"D4E18974327DE591A8318E83310DFA47198E588D4097579D1FCDFF3DC273209B"),
 "SITES_COHOMOLOGY_038_079_INDEPENDENT_REVIEW_20260831.json": (LANE,39042,"D89961406CA250C13E538F0AD9F241838C65522F130F57CB99CC5ED2AF622059"),
 "SITES_COHOMOLOGY_LINKED_053_REVIEW_20260831.json": (LANE,None,"BB7E3F8AF6A2C7A1D83976149E2BF3D754F8CFB910E9970975C138CB101C7E54"),
 "SITES_COHOMOLOGY_SOURCE_EMENDATIONS.json": (PRODUCER,26075,"DA3BFA3A8E310D2A5426BDA63F05F4AC3AFFA212BF5DCB9CA36CD03382F186E3"),
 "SITES_COHOMOLOGY_SOURCE_DEFECT_LEDGER.csv": (PRODUCER,33575,"34C199DD53EF7B37A4143F0C7B5F1EEED91BE523A016F217182A8D9A3F5F4A92"),
}
def sha(b):
 return hashlib.sha256(b).hexdigest().upper()
def load(p):
 return json.loads(p.read_text(encoding="utf-8-sig"))
def write(rel,b):
 p=ROOT/rel
 assert p.resolve().is_relative_to(ROOT)
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_bytes(b)
def dump(rel,obj):
 write(rel,(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode())
def jsonl(rel,rows):
 write(rel,("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in rows)).encode())
def sanitize(v):
 if isinstance(v,str):
  return v.replace(str(WORKSPACE),"<WORKSPACE>").replace(WORKSPACE.as_posix(),"<WORKSPACE>")
 if isinstance(v,list): return [sanitize(x) for x in v]
 if isinstance(v,dict): return {k:sanitize(x) for k,x in v.items()}
 return v
def identity(rel):
 b=(ROOT/rel).read_bytes()
 return {"path":rel,"bytes":len(b),"sha256":sha(b)}
def main():
 parser=argparse.ArgumentParser()
 parser.add_argument("--materialize",action="store_true")
 args=parser.parse_args()
 if not args.materialize: parser.error("Use --materialize for source preparation only.")
 if (ROOT/"candidate.manifest.json").exists() or list((ROOT/"builds").glob("*.pdf")):
  raise RuntimeError("Refusing to reset a built or manifest-bound candidate; preserve later-stage evidence.")
 lease=load(ROOT/"LEASE.json")
 assert lease["lease_id"]==LEASE and lease["status"]=="active"
 events=load(REPO/"registry/leases.json")["events"]
 matches=[e for e in events if e.get("lease_id")==LEASE]
 assert matches[-1]["event"]=="issued" and matches[-1]["state"]=="active"
 assert matches[-1]["candidate_path"]=="candidates/commons/stacks/errata/r39"
 assert (ROOT/".gitattributes").read_bytes()==b"* -text\n"
 snapshots={}
 evidence=[]
 for name,(directory,size,expected) in EXPECTED.items():
  raw=(directory/name).read_bytes()
  assert sha(raw)==expected and (size is None or len(raw)==size), name
  role="producer" if directory==PRODUCER else "registrar"
  rel="authority/"+role+"/"+name
  if name.endswith(".json"):
   obj=json.loads(raw)
   snapshots[name]=obj
   sanitized=sanitize(obj)
   sanitized["original_private_transport_identity"]={"bytes":len(raw),"sha256":sha(raw),"filename":name}
   dump(rel,sanitized)
  else:
   text=sanitize(raw.decode("utf-8-sig"))
   write(rel,text.encode("utf-8"))
  evidence.append(identity(rel))
 plan=snapshots["SITES_COHOMOLOGY_R39_DEDUP_AND_PLAN_20260831.json"]
 review=snapshots["SITES_COHOMOLOGY_038_079_INDEPENDENT_REVIEW_20260831.json"]
 assert plan["registry_head"]==HEAD
 source=(UPSTREAM/SOURCE).read_bytes()
 assert len(source)==534394 and sha(source)==AUTH_SHA
 write("authority/source/"+SOURCE,source)
 write("authority/COPYING",(UPSTREAM/"COPYING").read_bytes())
 dump("authority/upstream.lock.json",{"commit":COMMIT,"tree":TREE,"source":SOURCE,"bytes":len(source),"sha256":AUTH_SHA,"url":"https://github.com/stacks/stacks-project/blob/"+COMMIT+"/"+SOURCE})
 originals={o["id"]:o for o in snapshots["SITES_COHOMOLOGY_SOURCE_EMENDATIONS.json"]["emendations"]}
 proof={r["producer_id"]:r for r in review["rows"]}
 original_lines=source.splitlines(keepends=True)
 ops=[]; units=[]; maps=[]; decisions=[]
 for u in plan["proposed_units"]:
  sid=u["stable_id"]; pid=u["producer_id"]
  group=[]
  for p in u["operations"]:
   oid=p["producer_operation_id"]
   if oid.startswith("CANON-"):
    old=review["linked_expansion"]["old"]; new=review["linked_expansion"]["new"]
    origin="registrar_linked_expansion"
   else:
    old=originals[oid]["old"]; new=originals[oid]["new"]
    origin="producer_packet"
   a=old.encode(); b=new.encode(); start=p["start_byte"]; end=p["end_byte_exclusive"]
   assert source[start:end]==a
   assert sha(a)==p["old_sha256"] and sha(b)==p["replacement_sha256"]
   assert len(a)==p["old_bytes"] and len(b)==p["replacement_bytes"]
   assert source[:start].count(b"\n")+1==p["line"]
   assert original_lines[p["line"]-1].count(a)==1
   op={**p,"source":SOURCE,"stable_id":sid,"producer_id":pid,"origin":origin,
       "class":u["classification"],"source_start_line":p["line"],"source_end_line":p["line"],
       "old_text":old,"replacement_text":new,"declared_line_occurrences":1,"file_occurrences":source.count(a)}
   group.append(op);ops.append(op)
  locus=SOURCE+":"+",".join(str(o["line"]) for o in group)
  unit={"id":sid,"source":SOURCE,"producer_id":pid,"producer_ids":[pid],
        "producer_aliases":[u["producer_subunit"]] if u.get("producer_subunit") else [],
        "class":u["classification"],"locus":locus,"operation_ids":[o["operation_id"] for o in group],
        "payload":"payload/"+SOURCE,"status":"accepted_source_proposal_not_admitted"}
  units.append(unit)
  maps.append({"schema":"mathematics-commons-stacks-source-map/v2","unit_id":sid,
    "source":SOURCE,"authority":"authority/source/"+SOURCE,"authority_sha256":AUTH_SHA,
    "payload":"payload/"+SOURCE,"producer_id":pid,"producer_ids":[pid],
    "producer_aliases":unit["producer_aliases"],"class":u["classification"],"locus":locus,
    "proof":proof[pid]["rationale"],"operations":group,
    "adverse_evidence":proof[pid]["rationale"] if u["classification"]=="editorial_or_notational_clarification" or pid.endswith("053") else None})
  decisions.append({"schema":"mathematics-commons-stacks-decision/v1","id":"R39-D"+str(len(decisions)+1).zfill(3),
    "timestamp_utc":STAMP,"choice":"materialize_source_proposal_not_admission","stable_id":sid,
    "producer_id":pid,"rationale":proof[pid]["rationale"],"supersedes":None})
 assert [u["id"] for u in units]==["MC-STK-ERR-"+str(n) for n in range(1359,1402)]
 assert len(ops)==61 and len({o["operation_id"] for o in ops})==61
 ordered=sorted(ops,key=lambda o:o["start_byte"])
 assert all(a["end_byte_exclusive"]<=b["start_byte"] for a,b in zip(ordered,ordered[1:]))
 payload=source
 for o in reversed(ordered):
  payload=payload[:o["start_byte"]]+o["replacement_text"].encode()+payload[o["end_byte_exclusive"]:]
 assert len(payload)==534485 and sha(payload)==PAYLOAD_SHA
 write("payload/"+SOURCE,payload)
 spec={"schema":"mathematics-commons-stacks-operation-spec/v1","source":SOURCE,"authority_sha256":AUTH_SHA,
       "apply_order":"descending_start_byte","operation_count":61,"operations":ops}
 for rel,obj in [("operation-spec",spec),("stable-units",{"schema":"mathematics-commons-stacks-stable-units/v1","authority_commit":COMMIT,"unit_count":43,"units":units})]:
  dump(rel+".json",obj);dump(rel+".input.json",obj)
 for rel,rows in [("source-map",maps),("decisions",decisions),("rejections",[])]:
  jsonl(rel+".jsonl",rows);jsonl(rel+".input.jsonl",rows)
 # Reconstruct unchanged intervals independently, proving no unlisted source edits.
 cursor=0; observed=0; unchanged=bytearray()
 for o in ordered:
  span=source[cursor:o["start_byte"]]
  assert payload[observed:observed+len(span)]==span
  observed+=len(span)
  replacement=o["replacement_text"].encode()
  assert payload[observed:observed+len(replacement)]==replacement
  observed+=len(replacement);unchanged.extend(span);cursor=o["end_byte_exclusive"]
 assert payload[observed:]==source[cursor:];unchanged.extend(source[cursor:])
 patterns={"labels":rb"\\label\{[^}]*\}","refs":rb"\\(?:ref|eqref)\{[^}]*\}",
 "environments":rb"\\(?:begin|end)\{[^}]*\}","inputs":rb"\\input\{[^}]*\}","cites":rb"\\cite(?:\[[^]]*\])?\{[^}]*\}"}
 structure={}
 for key,pattern in patterns.items():
  before=re.findall(pattern,source);after=re.findall(pattern,payload)
  assert before==after,key
  structure[key]={"authority":len(before),"candidate":len(after),"ordered_equal":True}
 validation={"schema":"stacks-r39-source-validation-v1","passed":True,"scope":"source-only exact replay; NOT build/render/independent-candidate validation",
 "semantic_units":43,"producer_rows":42,"operations":61,"line_preimages_exact":61,"nonoverlapping":True,
 "unlisted_byte_changes":0,"unchanged_interval_sha256":sha(bytes(unchanged)),"structure":structure,
 "authority":identity("authority/source/"+SOURCE),"payload":identity("payload/"+SOURCE),
 "deduplication":sanitize(plan["deduplication"]),"build":"NOT_PERFORMED","visual_qa":"NOT_PERFORMED","independent_candidate_replay":"NOT_PERFORMED"}
 dump("source-validation.json",validation)
 inventory={"schema":"stacks-r39-formula-diagram-inventory-v1","source":SOURCE,"structure":structure,
 "operation_bound_math_changes":True,"note":"No assertion of complete math identity:61 explicit operations include intended formula/notation changes. Unchanged byte intervals proved exact.",
 "operations":[{"id":o["operation_id"],"line":o["line"],"class":o["class"],"old":o["old_text"],"new":o["replacement_text"]} for o in ops]}
 dump("formula-diagram-inventory.json",inventory)
 dump("authority/adverse/ROW053_ORIGINAL_AND_LINKED_EXPANSION.json",{
 "original_producer_proposal":originals["C-026"],"original_proposal_is_correct_but_incomplete":True,
 "linked_statement_operation":[o for o in ops if o["origin"]=="registrar_linked_expansion"],
 "independent_review":"authority/registrar/SITES_COHOMOLOGY_LINKED_053_REVIEW_20260831.json",
 "editorial_not_mathematical_falsehood_ids":["MC-STK-ERR-1371","MC-STK-ERR-1378","MC-STK-ERR-1400","MC-STK-ERR-1401"],
 "067_split":"MC-STK-ERR-1388/1389 preserve the same producer067 identity for two independent grammar defects."})
 config={"schema":"mathematics-commons-stacks-errata-candidate-config/v1","candidate_id":CID,
 "namespace":"commons/stacks/errata/r39","lease_id":LEASE,"writer_task":lease["writer_task"],
 "authority_commit":COMMIT,"authority_tree":TREE,"source_date_epoch":"1788181807","accepted":43,"rejected":0,"unresolved":0,
 "operation_count":61,"expected_unit_ids":[u["id"] for u in units],
 "expected_producer_ids":sorted({u["producer_id"] for u in units}),
 "payload_expected_bytes":len(payload),"payload_expected_sha256":PAYLOAD_SHA,
 "stems":{"sites-cohomology":{"authority_bytes":len(source),"authority_sha256":AUTH_SHA,"payload_bytes":len(payload),"payload_sha256":PAYLOAD_SHA,"build_exceptions":{} }},
 "proof_closure":{"accepted":43,"operations":61,"producer_rows":42,"linked_expansion_operations":1,"rejected":0,"unresolved":0},
 "build_render_admission_status":"NOT_PERFORMED","independent_replay":"not_performed",
 "authority_evidence":evidence}
 dump("candidate.config.json",config);dump("candidate.config.input.json",config)
 dump("builds/PENDING.json",{"schema":"stacks-r39-build-ready-state-v1","build":"NOT_PERFORMED",
 "deterministic_pdf_replay":"NOT_PERFORMED","render":"NOT_PERFORMED","visual_inspection":"NOT_PERFORMED",
 "independent_candidate_replay":"NOT_PERFORMED","admission":"NOT_PERFORMED","reason":"This worker is authorized for source preparation only."})
 dump("REGENERATION_RECEIPT.json",{"schema":"stacks-r39-source-regeneration-v1","status":"SOURCE_REPLAY_PASS_BUILD_PENDING",
 "pipeline":identity("pipeline_r39.py"),"source_validation":identity("source-validation.json"),
 "operation_spec":identity("operation-spec.json"),"stable_units":identity("stable-units.json"),
 "source_map":identity("source-map.jsonl"),"payload":identity("payload/"+SOURCE),
 "no_final_manifest":True,"next_command":"python replay-build.py --upstream-root <PINNED_SOURCE_DIRECTORY> --work-root <NEW_R39_WORK_DIRECTORY> --private-evidence-root <R39_PRIVATE_EVIDENCE_DIRECTORY>",
 "write_scope":"Only leased r39 candidate; no build was executed."})
 for p in ROOT.rglob("*"):
  if p.is_file() and p.suffix in (".json",".jsonl",".md",".csv",".py"):
   assert str(WORKSPACE).encode() not in p.read_bytes(),p.name
 print(json.dumps({"source_pass":True,"units":43,"operations":61,"payload_sha256":sha(payload),"build":"NOT_PERFORMED"}))
if __name__=="__main__": main()
