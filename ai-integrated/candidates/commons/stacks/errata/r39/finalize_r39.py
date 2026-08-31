"""Additive finalization. --mechanical is safe before visual review; --prepare and --seal fail closed."""
import argparse,hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
from pypdf import PdfReader
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[4]
PRIVATE=REPO.parent/"03_projects/language_management/cjk/03_working_translations/stacks_cjk_20260821/canon/private_evidence/errata-r39-20260831/render"
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest().upper()
def load(p): return json.loads(p.read_text(encoding="utf-8"))
def ev(p): return {"path":p.relative_to(ROOT).as_posix(),"bytes":p.stat().st_size,"sha256":sha(p)}
def dump(p,x):
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps(x,indent=2,sort_keys=True)+"\n",encoding="utf-8",newline="")
def bound(row):
 p=(ROOT/row["path"]).resolve()
 assert p.is_relative_to(ROOT) and p.is_file(),row["path"]
 assert sha(p)==row["sha256"] and p.stat().st_size==row.get("bytes",p.stat().st_size),row["path"]
def mechanical():
 source_review=load(ROOT/"replay/SOURCE_INDEPENDENT_VALIDATION.json")
 assert source_review["passed"] is True
 for row in source_review["observed_files"]: bound(row)
 for name in ("builds/build-receipt.json","builds/deterministic-replay.json"):
  assert load(ROOT/name)["passed"] is True,name
 br=load(ROOT/"builds/build-receipt.json")
 chapter=br["chapters"][0]
 for name in ("candidate_source","authority_source","candidate_pdf","authority_pdf","candidate_log","authority_log"):
  bound(chapter[name])
 pdf=ROOT/chapter["candidate_pdf"]["path"]
 reader=PdfReader(pdf)
 pages=len(reader.pages)
 assert pages==chapter["candidate_log_summary"]["pages"]==134
 rect_bad=[];links=0
 for n,page in enumerate(reader.pages,1):
  box=[float(x) for x in page.mediabox]
  for annotation in page.get("/Annots",[]):
   obj=annotation.get_object()
   if obj.get("/Subtype")!="/Link": continue
   links+=1
   rect=obj.get("/Rect")
   if not rect or len(rect)!=4: rect_bad.append(n);continue
   x0,y0,x1,y1=map(float,rect)
   if x0>x1 or y0>y1 or x0<box[0]-.01 or y0<box[1]-.01 or x1>box[2]+.01 or y1>box[3]+.01:rect_bad.append(n)
 assert not rect_bad,rect_bad
 stage=load(ROOT/"builds/BUILD_RENDER_STAGE.json")
 manifest=PRIVATE/"render-manifest.json"
 assert sha(manifest)==stage["render"]["sha256"]
 renders=load(manifest)
 rows=renders["pdfs"]["sites-cohomology"]["renders"]
 assert [x["page"] for x in rows]==list(range(1,pages+1))
 assert renders["pdfs"]["sites-cohomology"]["pdf_sha256"]==sha(pdf)
 all_rows=[(PRIVATE/"sites-cohomology"/r["file"],r) for r in rows]
 all_rows += [(PRIVATE/"contact_sheets"/r["file"],r) for r in renders["contact_sheets"]]
 all_rows += [(PRIVATE/"highres"/r["file"],r) for r in renders["high_resolution"]["renders"]]
 for p,r in all_rows: assert sha(p)==r["sha256"] and p.stat().st_size==r["bytes"],p.name
 mapping=load(ROOT/"builds/source-page-map.json")
 assert mapping["operation_spec"]["sha256"]==sha(ROOT/"operation-spec.json")
 assert mapping["auxiliary_build"]["candidate_pdf_sha256"]==sha(pdf)
 assert len(mapping["operations"])==61
 high=[r["page"] for r in renders["high_resolution"]["renders"]]
 assert set(mapping["unique_pages"]).issubset(high)
 report={"schema":"stacks-r39-final-mechanical-validation-v1","passed":True,
 "scope":"Source-stage identity preservation, build binding, repeated-PDF identity, PDF link geometry, render bytes and source-page mapping. Not visual inspection.",
 "source_independent_validation":ev(ROOT/"replay/SOURCE_INDEPENDENT_VALIDATION.json"),
 "preserved_source_stage_bindings":source_review["observed_files"],
 "build_receipt":ev(ROOT/"builds/build-receipt.json"),"deterministic_replay":ev(ROOT/"builds/deterministic-replay.json"),
 "source_page_map":ev(ROOT/"builds/source-page-map.json"),"build_render_stage":ev(ROOT/"builds/BUILD_RENDER_STAGE.json"),
 "candidate_pdf":ev(pdf),"pdf_pages":pages,"links":links,"bad_link_rectangles":rect_bad,
 "tagged_pdf":"/StructTreeRoot" in reader.trailer["/Root"],"render_artifacts_rehashed":len(all_rows),
 "source_operations_mapped":61,"high_resolution_pages":high,
 "visual_inspection":"NOT_ASSERTED","full_independent_final_review":"NOT_PERFORMED",
 "adverse_evidence":["Authority and candidate have21 overfull diagnostics each; actual visual disposition is separate.","Unresolved cross-chapter reference multisets match authority; standalone AUX set is incomplete."]}
 dump(ROOT/"builds/FINAL_MECHANICAL_VALIDATION.json",report)
 return report
def visual_receipts():
 aggregate=ROOT/"replay/VISUAL_ADJUDICATION.json"
 adjudication=load(aggregate)
 assert adjudication.get("passed") is True,"visual adjudication must explicitly pass"
 assert adjudication.get("pdf_sha256")==sha(ROOT/"builds/sites-cohomology.pdf"),"visual PDF binding mismatch"
 assert adjudication.get("covered_pages")==list(range(1,135)),"visual coverage must be exact1..134"
 assert adjudication.get("blocking_findings")==[],"unresolved blocking visual findings"
 reviews=adjudication.get("reviews")
 assert isinstance(reviews,list) and len(reviews)==3,"exactly three raw reviews required"
 expected=[("replay/VISUAL_PAGES_001_048.json",1,48),("replay/VISUAL_PAGES_049_096.json",49,96),("replay/VISUAL_PAGES_097_134.json",97,134)]
 ordered=sorted(reviews,key=lambda row:row["page_start"])
 for row,(path,a,b) in zip(ordered,expected):
  assert (row["path"],row["page_start"],row["page_end"])==(path,a,b),"review ranges must be disjoint and contiguous"
  assert isinstance(row.get("method"),str) and row["method"].strip(),"actual inspection method required"
  bound(row)
  # Parse, but do not normalize or rewrite the independently authored schemas.
  assert isinstance(load(ROOT/path),dict),"raw review must be a JSON object"
 # Aggregate and raw files are bound intact: their distinct methods, caveats,
 # inherited external references, wide diagrams, and accessibility limits remain.
 return [ev(aggregate)]+[ev(ROOT/row["path"]) for row in ordered]
def prepare():
 assert load(ROOT/"builds/FINAL_MECHANICAL_VALIDATION.json")["passed"] is True
 visuals=visual_receipts()
 destination=ROOT/"replay/FINAL_STAGE.json"
 if destination.exists(): raise FileExistsError("Final-stage snapshot is immutable; make a successor instead.")
 excluded={"candidate.manifest.json","replay/FINAL_STAGE.json","replay/FINAL_INDEPENDENT_REVIEW.json"}
 inventory=[ev(p) for p in sorted(ROOT.rglob("*")) if p.is_file() and p.relative_to(ROOT).as_posix() not in excluded and "__pycache__" not in p.parts and p.suffix!=".pyc"]
 report={"schema":"stacks-r39-final-stage-snapshot-v1","candidate_id":"stacks-errata-a04446e-r39",
 "status":"READY_FOR_FULL_INDEPENDENT_REVIEW_NOT_ADMITTED","visual_receipts":visuals,"snapshot_inventory":inventory,
 "mechanical_validation":ev(ROOT/"builds/FINAL_MECHANICAL_VALIDATION.json"),
 "independent_replay":"not_performed",
 "historical_states":"Source-stage NOT_PERFORMED fields and builds/PENDING.json are retained historical receipts, not current final-stage claims.",
 "closure_order":"Freeze this snapshot; independent agent reviews bytes and writes FINAL_INDEPENDENT_REVIEW.json binding this SHA; then seal top manifest last. Never rewrite this snapshot to hash the later review."}
 dump(destination,report)
 return report
def seal():
 snapshot=ROOT/"replay/FINAL_STAGE.json"
 stage=load(snapshot)
 for row in stage["snapshot_inventory"]: bound(row)
 replay_path=ROOT/"replay/FINAL_INDEPENDENT_REVIEW.json"
 independent=load(replay_path)
 assert independent.get("passed") is True
 assert independent.get("final_stage_sha256")==sha(snapshot)
 visuals=visual_receipts()
 config=load(ROOT/"candidate.config.json")
 primary={"stable_unit_manifest":"stable-units.json","source_map":"source-map.jsonl","decision_ledger":"decisions.jsonl","rejection_ledger":"rejections.jsonl","formula_diagram_inventory":"formula-diagram-inventory.json"}
 authorities=sorted(p for p in (ROOT/"authority").rglob("*") if p.is_file())
 excluded={p.resolve() for p in authorities}|{(ROOT/p).resolve() for p in primary.values()}|{(ROOT/"candidate.manifest.json").resolve()}
 others=[p for p in sorted(ROOT.rglob("*")) if p.is_file() and p.resolve() not in excluded and "__pycache__" not in p.parts and p.suffix!=".pyc"]
 m={"schema":"mathematics-commons-stacks-candidate-manifest/v1","candidate_id":config["candidate_id"],
 "lease_id":config["lease_id"],"namespace":config["namespace"],"writer_task":config["writer_task"],
 "upstream":{"lock":"upstream/stacks.lock.json","commit":config["authority_commit"],"tree":config["authority_tree"]},
 "source_authorities":[ev(p) for p in authorities],"source_closure":{"enumerated":True,"expected_units":43,"manifested_units":43,"complete":True},
 **{k:ev(ROOT/v) for k,v in primary.items()},"builds":[ev(p) for p in others],
 "rights_state":"Upstream GNU FDL rights are preserved in authority/COPYING; this independently prepared AI correction overlay is not an official Stacks Project edition or endorsement.",
 "review_state":"performed","independent_replay":"passed",
 "unresolved_defects":["Standalone cross-chapter reference warnings match authority; cumulative AUX is not supplied. Accessibility tagging is not asserted."],
 "stop_conditions":["A changed referenced byte invalidates this final manifest. Registry admission and composition remain separate."],
 "generated_at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z")}
 schema=load(REPO/"schemas/candidate-manifest.schema.json")
 Draft202012Validator(schema).validate(m)
 dump(ROOT/"candidate.manifest.json",m)
 return {"manifest":ev(ROOT/"candidate.manifest.json"),"schema_errors":0}
def main():
 p=argparse.ArgumentParser()
 p.add_argument("stage",choices=["mechanical","prepare","seal"])
 args=p.parse_args()
 result={"mechanical":mechanical,"prepare":prepare,"seal":seal}[args.stage]()
 print(json.dumps({"stage":args.stage,"passed":True,"result":result.get("status",result.get("manifest",result.get("scope")))}))
if __name__=="__main__":main()
