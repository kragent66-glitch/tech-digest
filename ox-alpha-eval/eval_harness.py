#!/usr/bin/env python3
"""
Ox Alpha eval harness — runs a curated task set against stealth/ox-alpha on the
Nous portal, captures raw evidence, and (for coding tasks) ACTUALLY EXECUTES the
generated code against hidden test cases so the verdict is verifiable.

Design notes for the blog:
- Free + same platform (Nous portal) => reproducible, zero-cost.
- Every coding task is run against a hidden tester via subprocess; the verdict
  is "PASSED all N hidden cases" or the actual failure output. Not the model's
  claim.
- Raw API JSON saved per task in evidence/.

Usage:
  python3 eval_harness.py [--model stealth/ox-alpha] [--tasks coding]
"""
import argparse, json, os, subprocess, sys, time, tempfile, re

NOUS_KEY = None
def load_key():
    global NOUS_KEY
    env = os.path.expanduser("~/.hermes/.env")
    if NOUS_KEY is None:
        for line in open(env):
            if line.startswith("NOUS_API_KEY="):
                NOUS_KEY = line.strip().split("=", 1)[1]
    return NOUS_KEY

BASE = "https://inference-api.nousresearch.com/v1"
EVIDENCE = os.path.expanduser("~/.hermes/ox-alpha-blog/evidence")

# Provider routing — keeps one identical harness against every backend.
# opencode-go (DeepSeek) is a gateway; model id is passed through, auth key in
# OPENCODE_GO_API_KEY, base_url per config.
PROVIDERS = {
    "stealth/ox-alpha":          {"base_url": BASE, "key_env": "NOUS_API_KEY",       "query_auth": True},
    "stepfun/step-3.7-flash:free":{"base_url": BASE, "key_env": "NOUS_API_KEY",      "query_auth": True},
    "tencent/hy3:free":          {"base_url": BASE, "key_env": "NOUS_API_KEY",       "query_auth": True},
    # opencode-go gateway (all models share base_url + OPENCODE_GO_API_KEY)
    "deepseek-v4-flash":         {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "ox-alpha-free":             {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "deepseek-v4-pro":           {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "kimi-k3":                   {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "glm-5.3":                   {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "glm-5.2":                   {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "minimax-m3":                {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "qwen3.8-max":               {"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    "muse-spark-1.2-contributor":{"base_url": "https://opencode.ai/zen/go/v1",       "key_env": "OPENCODE_GO_API_KEY", "query_auth": False, "go_gateway": True},
    # ZAI gateway (skipped for now - hard rate limit)
    "glm-4.7-flash":             {"base_url": "https://api.z.ai/api/paas/v4",        "key_env": "ZAI_API_KEY",        "query_auth": False},
}

def _env_value(env_k, env_map):
    try:
        return env_map[env_k]
    except KeyError:
        pass
    try:
        for line in open(os.path.expanduser("~/.hermes/.env")):
            if line.startswith(env_k + "="):
                return line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return ""

def call_model(model, system, user, max_tokens=3000, temperature=1, timeout=240, retries=8):
    import urllib.request, urllib.error, os as _os
    prov = PROVIDERS.get(model)
    if not prov:
        raise RuntimeError(f"no provider routing for model {model}")
    base = prov["base_url"]
    key = _env_value(prov["key_env"], _os.environ)
    throttle = float(_os.environ.get("OX_THROTTLE", "0"))
    retries = int(_os.environ.get("OX_RETRIES", str(retries)))
    base_wait = float(_os.environ.get("OX_BASE_WAIT", "2"))
    payload = {
        "model": model,
        "messages": (
            ([{"role": "system", "content": system}] if system else [])
            + [{"role": "user", "content": user}]
        ),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if prov.get("query_auth"):
        url = f"{base}/chat/completions?subkey={key}&apiKey={key}"
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json",
                   "User-Agent": "ox-alpha-eval-harness/1.0"}
    else:
        url = f"{base}/chat/completions"
        headers = {"Authorization": f"Bearer {key}",
                   "Content-Type": "application/json",
                   "User-Agent": "ox-alpha-eval-harness/1.0"}
    body = json.dumps(payload).encode()
    last = None
    for attempt in range(retries):
        if throttle:
            time.sleep(throttle)
        try:
            req = urllib.request.Request(url, data=body, headers=headers)
            start = time.time()
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
            return json.loads(raw), time.time() - start
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429, 500, 502, 503, 504):
                wait = base_wait * (2 ** attempt)
                print(f"    [retry {attempt+1}] HTTP {e.code}, waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(base_wait * (2 ** attempt))
            continue
    raise RuntimeError(f"exhausted retries calling {model}: {last}")

def extract_python(text):
    # last python code fence, fall back to whole text
    blocks = re.findall(r"```(?:python|py)?\n(.*?)```", text, re.DOTALL)
    if blocks:
        return max(blocks, key=len)
    return text

def extract_answer(raw_response):
    """Get the final answer text from a response, handling the two API
    contracts seen across models:
      - content filled (Ox Alpha): use content.
      - content null + reasoning filled (step-3.7-flash:free): a real client
        reading the stream sees the reasoning; use the final code block from it
        (so we don't unfairly score an empty answer when the model DID produce
        code in its reasoning trace)."""
    msg = raw_response["choices"][0]["message"]
    content = msg.get("content")
    if isinstance(content, str) and content.strip():
        return content
    reasoning = msg.get("reasoning") or ""
    if isinstance(reasoning, list):
        reasoning = "".join(str(x) for x in reasoning)
    for rdetail in msg.get("reasoning_details") or []:
        reasoning += "\n" + str(rdetail.get("text", ""))
    return reasoning or ""

def run_python(code, test_code, case_name, timeout=20):
    """Execute `code` + `test_code` in a fresh subprocess. Return (ok, detail)."""
    probe = code + "\n\n# ==== tests ====\n" + test_code
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(probe)
        path = f.name
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           timeout=timeout)
        if p.returncode != 0:
            return False, f"EXIT {p.returncode}\nSTDERR:\n{p.stderr[-1500:]}"
        return True, p.stdout.strip()[-2000:]
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    finally:
        os.unlink(path)

# ============================================================ CODING TASKS
CODING_TASKS = []

# T1: two_sum (correctness + all return value details)
CODING_TASKS.append({
    "id": "two_sum",
    "title": "Two Sum (fast path)",
    "task_statement": """Write a Python function `def two_sum(nums: list[int], target: int) -> list[int]` that returns the indices of the two numbers that add up to target. Use a single pass with a dict. Return exactly [i, j] where i<j. Return an empty list if none. Output ONLY the function definition, no tests, no explanation.""",
    "hidden_tests": """assert two_sum([2,7,11,15], 9) == [0,1]
assert two_sum([3,2,4], 6) == [1,2]
assert two_sum([3,3], 6) == [0,1]
assert two_sum([1,2,3], 7) == []
assert two_sum([0,4,3,0], 0) == [0,3]
print("ALL_PASS")""",
})

# T2: LRU cache
CODING_TASKS.append({
    "id": "lru_cache",
    "title": "LRU Cache (O(1))",
    "task_statement": """Write a Python `class LRUCache:` implementing an O(1) LRU cache: `__init__(self, capacity: int)`, `get(self, key:int)->int` (return -1 if absent), `put(self, key:int, value:int)->None`. It must be O(1) (use an OrderedDict or linked list). Output ONLY the class.""",
    "hidden_tests": """c = LRUCache(2)
c.put(1,1); c.put(2,2)
assert c.get(1)==1
c.put(3,3)            # evicts key 2
assert c.get(2)==-1
c.put(4,4)            # evicts key 1
assert c.get(1)==-1
assert c.get(3)==3
assert c.get(4)==4
print("ALL_PASS")""",
})

# T3: number of islands
CODING_TASKS.append({
    "id": "num_islands",
    "title": "Number of Islands (DFS/BFS)",
    "task_statement": """Write `def num_islands(grid: list[list[str]]) -> int` counting connected '1' regions (4-directional). Mutate a copy to avoid recursion limits on big grids. Output ONLY the function.""",
    "hidden_tests": """g1=[["1","1","1","1","0"],["1","1","0","1","0"],["1","1","0","0","0"],["0","0","0","0","0"]]
g2=[["1","1","0","0","0"],["1","1","0","0","0"],["0","0","1","0","0"],["0","0","0","1","1"]]
assert num_islands(g1)==1
assert num_islands(g2)==3
assert num_islands([["1"]])==1
assert num_islands([["0"]])==0
print("ALL_PASS")""",
})

# T4: find median of two sorted arrays (hard, binary search)
CODING_TASKS.append({
    "id": "median_two_arrays",
    "title": "Median of Two Sorted Arrays (O(log(n+m)))",
    "task_statement": """Write `def find_median_sorted_arrays(a: list[int], b: list[int]) -> float` in O(log(n+m)) using binary search (not concatenate+sort). Output ONLY the function.""",
    "hidden_tests": """import random
def brute(a,b):
    x=sorted(a+b)
    if not x: return None
    if len(x)%2==1: return float(x[len(x)//2])
    return (x[len(x)//2-1]+x[len(x)//2])/2.0
assert abs(find_median_sorted_arrays([1,3],[2])-2.0) < 1e-9
assert abs(find_median_sorted_arrays([1,2],[3,4])-2.5) < 1e-9
assert find_median_sorted_arrays([],[1])==1.0
assert find_median_sorted_arrays([],[2,3])==2.5
assert find_median_sorted_arrays([1,3,5],[2,4])==3.0
for _ in range(300):
    a=sorted(random.sample(range(-50,50), random.randint(1,9)))
    b=sorted(random.sample(range(-50,50), random.randint(1,9)))
    m=brute(a,b)
    assert abs(find_median_sorted_arrays(a,b)-m)<1e-9, (a,b)
print("ALL_PASS")""",
})

# T5: reverse words in a string (real-world string handling)
CODING_TASKS.append({
    "id": "reverse_words",
    "title": "Reverse Words in a String",
    "task_statement": """Write `def reverse_words(s: str) -> str`: given a string with multiple spaces, return a string of the words in reverse order, each separated by a single space, no leading/trailing space. Output ONLY the function.""",
    "hidden_tests": """assert reverse_words("the sky is blue")=="blue is sky the"
assert reverse_words("  hello world  ")=="world hello"
assert reverse_words("a good   example")=="example good a"
assert reverse_words("  Bob    Loves  Alice   ")=="Alice Loves Bob"
print("ALL_PASS")""",
})

# T6: JSON pretty printer with proper escaping (real-world, subtle)
CODING_TASKS.append({
    "id": "json_escape",
    "title": "Correct JSON string escaping (subtle)",
    "task_statement": """Write `def json_escape(s: str) -> str` that returns a valid JSON string literal for s with correct escaping: double quotes, backslash, newline, tab, carriage return, backspace, form feed (\\b \\f), and control chars < 0x20 as \\uXXXX. Do NOT use json.dumps (implement it). Other unicode should pass through. Output ONLY the function.""",
    "hidden_tests": """import json
def check(s):
    lit = '"' + json_escape(s) + '"'   # wrap as a JSON string literal
    assert json.loads(lit) == s, (lit, s)
check('he said "hi"')
check('a\\\\b')
check('a\\nb')
check('a\\tb')
check('a\\rb')
check('a\\bb')
check('a\\fb')
check('ctrl:'+chr(1))
check('h\\xE9llo')   # non-ASCII passes through
check('mix \\"and\\\\ slash\\n newline')
print("ALL_PASS")""",
})

# T7: multi-key stable sort (real-world)
CODING_TASKS.append({
    "id": "multi_key_sort",
    "title": "Multi-key stable sort",
    "task_statement": """Write `def sort_events(events: list[dict]) -> list[dict]` sorting a list of event dicts by (priority DESC numeric, then start_time ASC numeric, then name ASC alphabetical). Stable. Output ONLY the function.""",
    "hidden_tests": """ev=[{"name":"b","priority":1,"start_time":5},{"name":"a","priority":2,"start_time":1},{"name":"c","priority":2,"start_time":1},{"name":"d","priority":1,"start_time":3}]
r=sort_events(ev)
assert [x["name"] for x in r]==["a","c","d","b"], r
# stability: equal keys keep original order
ev2=[{"name":"x","priority":1,"start_time":1},{"name":"y","priority":1,"start_time":1}]
assert [x["name"] for x in sort_events(ev2)]==["x","y"]
print("ALL_PASS")""",
})

# ============================================================ REASONING TASKS
REASONING_TASKS = []

REASONING_TASKS.append({
    "id": "river_crossing",
    "title": "Logic: river crossing puzzle",
    "task_statement": """A farmer, a fox, a chicken, and a sack of grain must cross a river with a boat that holds the farmer plus at most ONE item. The fox will eat the chicken and the chicken will eat the grain if the farmer is absent from a bank. Give a step-by-step sequence that gets everyone across with nothing eaten. Answer as a numbered list of moves, e.g. '1. Farmer takes X across'. Output ONLY the moves.""",
    "grader": "logic_river",
})

REASONING_TASKS.append({
    "id": "monty_hall",
    "title": "Logic: Monty Hall with explicit reasoning",
    "task_statement": """Monty Hall: three doors, one prize. You pick door 1. Monty (who knows where the prize is) opens an empty door. Should you switch? If you switch, what is your exact probability of winning? Give the numeric probability and a one-sentence why. Output JUST the number and one sentence.""",
    "grader": "logic_monty",
})

REASONING_TASKS.append({
    "id": "sq_sum_formula",
    "title": "Math: sum of first N squares",
    "task_statement": """What is the closed-form formula for 1^2 + 2^2 + ... + n^2? Give the exact rational expression in terms of n, and what it equals for n=100. Output the formula and the number.""",
    "grader": "math_sqsum",
})

REASONING_TASKS.append({
    "id": "partial_trap",
    "title": "Math: partial fractions trap",
    "task_statement": """Evaluate sum_{k=1}^{n} 1/(k(k+1)). Give the closed form in terms of n, the limit as n->infinity, and specifically what it equals for n=1,000,000 (give the exact value as a fraction or decimal, not an approximation with '...'). Output the closed form, the limit, and the n=1,000,000 value.""",
    "grader": "math_partial",
})

# ============================================================ AGENTIC / TOOL
TOOL_TASKS = []

TOOL_TASKS.append({
    "id": "tool_weather_call",
    "title": "Tool calling: weather lookup",
    "task_statement": """Given tools get_weather(city) and get_time(city), the user asks: 'Should I bring an umbrella tomorrow in Seattle? Also give me the briefing time.' Call the tools with correct JSON arguments. Then produce a final answer. Return your tool calls as JSON lines: {"tool":"get_weather","args":{"city":"Seattle"}} then {"tool":"get_time",...} then a final sentence after a 'FINAL:' marker. The simulated results are: get_weather("Seattle") -> {"forecast":"rain","probability":0.8}; get_time("Seattle") -> "10:00". A correct answer says umbrella yes or asks you don't have 'tomorrow' data; at minimum it makes the two tool calls in order and references rain>=0.8. Output your tool calls then FINAL: <answer>.""",
    "grader": "tool_weather",
})

# ============================================================
def _norm_math(text: str) -> str:
    """Normalize mathy text: strip LaTeX commands/brace-grouping so value-checks
    are robust to rendering (e.g. \frac{n}{n+1} -> nn+1)."""
    import re
    s = text
    s = s.replace("{,}", "").replace(",", "")
    # \frac{a}{b} -> ab ; spelunk nested braces by matching balanced groups
    while "\\frac" in s:
        for m in re.finditer(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", s):
            s = s.replace(m.group(0), "(" + m.group(1) + "/" + m.group(2) + ")", 1)
            break
        else:
            s = s.replace("\\frac", "")
    s = s.replace("\\frac", "")
    for cmd in ["circ", "infty", "tfrac", "dfrac", "text", "boxed", "left", "right", "big", "Big", "lim", "sum"]:
        s = s.replace("\\" + cmd, "")
    s = s.replace("{", "").replace("}", "")
    s = s.replace(" ", "")
    s = s.replace("\\\\", "\\")
    return s


def grade_reasoning(task_id, text):
    low = text.lower()
    if task_id == "river_crossing":
        required = {"fox", "chicken", "grain"}
        found = {w for w in required if w in low}
        # A correct solution moves the chicken first, returns it at least once,
        # and never leaves fox+chicken or chicken+grain alone. Accept the
        # several phrasings each model uses for the chicken's return trip.
        chicken_return = any(k in low for k in [
            "bring chicken back", "chicken back", "return the chicken",
            "returns with chicken", "returns the chicken", "return with chicken",
            "brings the chicken back", "brings chicken back", "takes the chicken back",
        ])
        return len(found) == 3 and chicken_return
    if task_id == "monty_hall":
        # A correct answer states the 2/3 probability and that the unopened
        # door (or switching) inherits the remaining probability. Accept the
        # several phrasings models use — the word "switch" is NOT required.
        has_23 = ("2/3" in text or "66" in text or "0.66" in text or "0.67" in text)
        inherits = any(k in low for k in [
            "switch", "unopened", "other door", "one-third", "1/3", "2/3",
            "remaining", "inherit",
        ])
        return has_23 and inherits
    if task_id == "sq_sum_formula":
        # Accept LaTeX form n(n+1)(2n+1)/6 and the number 338350 (which models
        # may render as 338,350 or 338{,}350).
        norm = _norm_math(text)
        ok_formula = "n(n+1)(2n+1)" in norm
        ok_num = "338350" in norm
        return ok_formula and ok_num
    if task_id == "partial_trap":
        # Closed form n/(n+1); limit 1; exact value at n=1,000,000 is
        # 1000000/1000001 = 0.9999990000009999... (NOT 999999/1000000).
        norm = _norm_math(text)
        ok_closed = ("n/(n+1)" in norm or "1-1/(n+1)" in norm or "n/n+1" in norm)
        ok_value = ("1000000/1000001" in norm or "0.999999" in norm or "1000000/1000001" in norm)
        return ok_closed and ok_value
    return False

# ============================================================
def run_coding(model, task):
    rec = {"task": task["id"], "title": task["title"], "model": model}
    resp, dt = call_model(model,
        system="You are a precise coding assistant. Output ONLY what is asked.",
        user=task["task_statement"], max_tokens=8192)
    rec["latency_s"] = round(dt, 2)
    rec["raw_response"] = resp
    content = extract_answer(resp)
    usage = resp.get("usage", {})
    rec["usage"] = usage
    rec["generated_code"] = content
    code = extract_python(content)
    ok, detail = run_python(code, task["hidden_tests"], task["id"])
    rec["verdict"] = "PASS" if ok else "FAIL"
    rec["execution_detail"] = detail
    rec["answer_origin"] = "content" if (resp["choices"][0]["message"].get("content")) else "reasoning"
    return rec

def run_reasoning(model, task):
    rec = {"task": task["id"], "title": task["title"], "model": model}
    resp, dt = call_model(model,
        system="You are a careful reasoning assistant.",
        user=task["task_statement"])
    rec["latency_s"] = round(dt, 2)
    content = extract_answer(resp)
    rec["raw_response"] = resp
    rec["usage"] = resp.get("usage", {})
    rec["answer_text"] = content
    rec["verdict"] = "PASS" if grade_reasoning(task["id"], content) else "FAIL"
    rec["rubric_notes"] = "rubric: see grader"
    rec["answer_origin"] = "content" if (resp["choices"][0]["message"].get("content")) else "reasoning"
    return rec

def run_tool(model, task):
    rec = {"task": task["id"], "title": task["title"], "model": model}
    resp, dt = call_model(model,
        system="You are an agent with access to tools.",
        user=task["task_statement"])
    rec["latency_s"] = round(dt, 2)
    content = extract_answer(resp)
    rec["raw_response"] = resp
    rec["usage"] = resp.get("usage", {})
    rec["answer_text"] = content
    rec["answer_origin"] = "content" if (resp["choices"][0]["message"].get("content")) else "reasoning"
    low = content.lower()
    # Case-insensitive: models may render the city as "Seattle" (caps).
    has_weather = "get_weather" in low and "seattle" in low
    has_time = "get_time" in low and "seattle" in low
    rec["tool_calls_made"] = {"weather": has_weather, "time": has_time}
    rec["verdict"] = "PASS" if (has_weather and has_time) else "FAIL"
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="stealth/ox-alpha")
    ap.add_argument("--tasks", default="all", choices=["all","coding","reasoning","tool","coding,reasoning"])
    args = ap.parse_args()

    model = args.model
    os.makedirs(EVIDENCE, exist_ok=True)
    results = []
    wanted = args.tasks

    def want(t): return t in wanted or wanted == "all" or wanted == "coding,reasoning" and t in ("coding","reasoning")

    if want("coding"):
        for t in CODING_TASKS:
            print(f"[coding] {t['id']} ...", flush=True)
            results.append(run_coding(model, t))
    if want("reasoning"):
        for t in REASONING_TASKS:
            print(f"[reasoning] {t['id']} ...", flush=True)
            results.append(run_reasoning(model, t))
    if want("tool"):
        for t in TOOL_TASKS:
            print(f"[tool] {t['id']} ...", flush=True)
            results.append(run_tool(model, t))

    dump = os.path.join(EVIDENCE, f"{model.replace('/','__')}_all.json")
    with open(dump, "w") as f:
        json.dump({"model": model, "results": results}, f, indent=2)
    print(f"\nSaved evidence -> {dump}")
    print(f"Total: {len(results)} tasks")
    for r in results:
        print(f"  {r['verdict']:5}  {r['task']}")
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\nSUMMARY: {passed}/{len(results)} passed ({100*passed//len(results)}%)")

if __name__ == "__main__":
    main()