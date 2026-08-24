#!/usr/bin/env python3
"""Re-grade saved evidence with a corrected rubric WITHOUT re-calling the API.

Preserves the original raw API response for each task (that's the evidence) but
recomputes the verdict using fixed graders. Coding verdicts are re-derived by
re-executing the generated code against hidden tests. Writing an AUDIT trail of
(original_verdict -> corrected_verdict, reason) so the blog can disclose the
grader fixes honestly."""
import json, importlib.util, os, sys

spec = importlib.util.spec_from_file_location("harness", "eval_harness.py")
h = importlib.util.module_from_spec(spec); spec.loader.exec_module(h)

EVIDENCE = os.path.expanduser("~/.hermes/ox-alpha-blog/evidence")

def regrade(path):
    d = json.load(open(path))
    model = d["model"]
    results = d["results"]
    audit = []
    for r in results:
        tid = r["task"]
        t = None
        for cand in (h.CODING_TASKS + h.REASONING_TASKS + h.TOOL_TASKS):
            if cand["id"] == tid:
                t = cand
        original = r["verdict"]
        reason = ""
        if tid in {x["id"] for x in h.CODING_TASKS}:
            code = h.extract_python(r.get("generated_code", ""))
            ok, det = h.run_python(code, t["hidden_tests"], tid)
            new = "PASS" if ok else "FAIL"
            if new != original:
                reason = f"re-ran hidden tests (original {original} -> {new}): {det.strip()[:150]}"
            r["running_verdict_recheck"] = det if not ok else "ALL_PASS"
        elif tid in {x["id"] for x in h.REASONING_TASKS}:
            text = r.get("answer_text", "")
            new = "PASS" if h.grade_reasoning(tid, text) else "FAIL"
            if new != original:
                reason = f"rubric corrected (original {original} -> {new})"
            r["rubric_recheck"] = new
        else:  # tool
            low = r.get("answer_text", "").lower()
            hw = "get_weather" in low and "seattle" in low
            ht = "get_time" in low and "seattle" in low
            new = "PASS" if (hw and ht) else "FAIL"
            if new != original:
                reason = f"rubric corrected case-insensitive (original {original} -> {new})"
            r["tool_recheck"] = {"weather": hw, "time": ht}
        r["verdict"] = new
        audit.append({"task": tid, "original": original, "corrected": new, "reason": reason})
    out = os.path.join(EVIDENCE, f"{model.replace('/','__')}_all.regraded.json")
    with open(out, "w") as f:
        json.dump({"model": model, "regraded": True, "results": results}, f, indent=2)
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    n = len(results)
    print(f"Regraded {model}: {passed}/{n} ({100*passed//n}%)")
    for a in audit:
        marker = "  " if a["original"] == a["corrected"] else "!!"
        print(f"  {marker} {a['task']:18} {a['original']} -> {a['corrected']}  {a['reason']}")
    print(f"Saved -> {out}\n")

if __name__ == "__main__":
    for p in sys.argv[1:] or [os.path.join(EVIDENCE, "stealth__ox-alpha_all.json")]:
        regrade(p)