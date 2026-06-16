import os
import json
import zipfile
import tempfile
import requests
import streamlit as st
from dotenv import load_dotenv
from pathlib import Path
from io import BytesIO
from jsonschema import validate
from datetime import datetime, timedelta
import time
import re
from copy import deepcopy

# -----------------------------
# LLM CHAT HISTORY (KEEP SAME)
# -----------------------------
chat_history = [
    {"role": "system", "content": "You are a Postman script conversion expert that follows specific conversion rules exactly. Never add extra code or comments."}
]

# -----------------------------
# Postman schema validation
# -----------------------------
def validate_as_v22_but_save_as_v21(obj):
    if "info" not in obj:
        obj["info"] = {}
    obj["info"]["schema"] = "https://schema.getpostman.com/json/collection/v2.2.0/collection.json"
    validate(instance=obj, schema=schema)
    obj["info"]["schema"] = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

# -----------------------------
# Load Env + Setup
# -----------------------------
load_dotenv()
api_url = os.getenv("AZURE_URL")

if not api_url or not api_url.strip():
    st.error("AZURE_URL is not set in your .env file or is empty. Please check your .env configuration.")
    st.stop()

schema = None
try:
    with open("postman_collection_v2.2_schema.json", "r", encoding="utf-8") as f:
        schema = json.load(f)
    st.warning("Loaded Postman v2.2 schema from local fallback.")
except Exception as fallback_error:
    st.error(f"Failed to load Postman schema.\n\nError: {fallback_error}")
    st.stop()

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="Postman Bulk Converter + Refactor")
st.title("Convert + Refactor Postman Collections from ZIP")

uploaded_zip = st.file_uploader("Upload a zipped folder of Postman collections (.zip)", type="zip")

enable_refactor = st.checkbox("Refactor common pm.test blocks into collection-level function", value=True)

# ============================================================
# ===================== CONVERTER (YOUR CODE) =================
# ============================================================

def generate_script_v22(old_script, type):
    prompt = f'''
<|system|>
You are a helpful assistant who is better than Postman's Postbot AI which fixes and converts old Postman scripts from legacy format (v2.1.0) to the modern format (v2.2.0). Retain the version as v2.1.0 in the schema only. If the script is empty, leave it empty.
You are converting Postman scripts from old format to new format. Your output must contain only one complete script block. No duplicates. No repetition. No partial rewrites. No stray tests. If a script is already in new format, do not convert it.

<|user|>
Convert the following Postman {type} script to modern syntax. Schema version should stay v2.1.0. If the following aren't followed properly, I will end up losing my job so please follow these.

ROLE
You are an expert Postman script migration engine.

Your task is to convert OLD VERSION Postman scripts into NEW VERSION Postman scripts.

CRITICAL NOTE:
If the scripts in the collection already exist in the new format (for example, tests already use pm.test() instead of tests["..."]), then DO NOT modify or add any extra lines to the script. In such cases, ONLY follow instructions 18, 19, and 20 below, and IGNORE all other instructions related to conversion.

GENERAL CONVERSION RULES:

PRIORITY 1 — BASE VARIABLES (MANDATORY)

In Post-response test scripts, define variables exactly as follows:

const response = pm.response.json();
const nr = response.data || {{}};
let request_path = pm.collectionVariables.get("request_url");

Rules:

* response is used only for code, message, and schema validation
* nr represents response.data
* Never use nr.data
* Never re-parse JSON

1. Preserve the original test logic and assertions exactly. Make only the minimum structural changes required by data type differences (for example, use [i] when accessing array elements).
2. Understand the JSON structure from the old script and access properties in the same logical way, but using the new syntax.
3. If the script is empty, return an empty string. If the script contains comments, remove them completely and DO NOT convert comments into executable code.
4. Do NOT add sample code, usage examples, placeholders, dummy property names, or the words "javascript" or "js". Do NOT add inline comments.
5. Return the FULL completed script with valid syntax and the same logic as the original.
6. If a constant object is a schema, retain it exactly as-is.
7. NEVER write pm.expect(nr.data).to.have.property(...)
   Use pm.expect(nr).to.have.property(...) instead.
   Use pm.expect(response.hasOwnProperty(...)) ONLY when validating data itself, code and message
   NEVER use pm.expect(response.hasOwnProperty(...)) for validating data-level properties
   ALWAYS use pm.expect(nr.hasOwnProperty(...))
8. There is NO function like pm.response.json().has(). Use hasOwnProperty safely.
9. DO NOT generate scripts that cause "no tests found" errors in Postman.
10. NEVER use pm.response in Pre-request scripts.
11. Preserve original test descriptions EXACTLY. Do not reword titles.
12. Do not add new variables or functions unless they existed in the original script.
13. Do not use JSON.parse(pm.response.json()).
14. Do not use pm.globals.get(...) in Pre-request scripts.
    Do not use pm.globals.set(...) in Test scripts unless the original script used them.
15. If a schema constant exists, copy it EXACTLY without modification.

REQUEST PATH HANDLING:
Collection / Folder Pre-request Script ONLY:

let unresolvedUrl = pm.request.url.toString().replace('{{{{domain}}}}', '').split('?')[0];
pm.collectionVariables.set("request_url", unresolvedUrl);

Never place this in test scripts.

OUTPUT REQUIREMENTS:
Return ONLY the converted script as plain JavaScript.
No markdown.
No explanations.
No comments.

{old_script}
'''
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": chat_history + [{"role": "user", "content": prompt}],
        "model": "gpt-4.1 mini"
    }
    response = requests.post(api_url, json=payload, timeout=1600)
    response.raise_for_status()
    return response.text.strip().strip('`\n"\' ')


def generate_postman_v22_again(oldpm_raw):
    prompt = f"""
<|system|>
You are a helpful assistant that corrects the format of the Postman v2.2.0 collection.

<|user|>
Update this collection to Postman v2.2.0 with proper test scripts (pm.test, pm.expect, pm.response). Retain v2.1.0 in the schema string.

```json
{oldpm_raw}
"""
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": chat_history + [{"role": "user", "content": prompt}],
        "model": "gpt-4.1-mini"
    }
    response = requests.post(api_url, json=payload, timeout=180)
    response.raise_for_status()
    fixed = response.text.strip().removeprefix("```json").removesuffix("```").strip()
    return fixed


def fix_syntax_v22(truncated_script, old_script):
    prompt = f'''
<|system|>
You are an expert Postman script fixer that completes truncated or incomplete Postman scripts by correcting syntax issues. Your job is to fix the syntax errors in the provided script without changing its logic, structure, or variable names.
<|user|>
Continue from where the previous LLM's response truncated and return only the missing part not including the word from where it ended and NOT THE ENTIRE SCRIPT. If it is already valid and completely converted into new format, return it as it is.
DO NOT change logic. Return JavaScript only.

Original script:
{old_script}

Truncated output:
{truncated_script}
'''
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "message": chat_history + [{"role": "user", "content": prompt}],
        "model": "gpt-4.1 mini"
    }
    response = requests.post(api_url, json=payload, timeout=3200)
    response.raise_for_status()
    return response.text.strip().strip('`\n"\' ')


def generate_script_v22_fix(truncated_script, original_script, script_type):
    prompt = f'''
<|system|>
You are a helpful assistant that completes truncated or incomplete Postman {script_type} scripts. Return full valid script.

<|user|>
Truncated:
{truncated_script}

Original:
{original_script}
'''
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": chat_history + [{"role": "user", "content": prompt}],
        "model": "gpt-4.1-mini"
    }
    response = requests.post(api_url, json=payload, timeout=3200)
    response.raise_for_status()
    return response.text.strip().strip('`\n"\' ')


def convert_scripts_in_collection(obj, parent_listen=None):
    if isinstance(obj, dict):
        if "event" in obj and isinstance(obj["event"], list):
            for event in obj["event"]:
                listen_type = event.get("listen", None)
                if "script" in event:
                    convert_scripts_in_collection(event, parent_listen=listen_type)

        if "script" in obj and isinstance(obj["script"], dict) and "exec" in obj["script"]:
            value = obj["script"]
            old_exec = value["exec"]

            if isinstance(old_exec, list) and all(line.strip() == "" for line in old_exec):
                value["exec"] = []
            else:
                script_text = "\n".join(old_exec) if isinstance(old_exec, list) else str(old_exec)
                try:
                    script_type = parent_listen if parent_listen in ("prerequest", "test") else "test"
                    new_script = generate_script_v22(script_text, script_type)
                    cleaned_script = new_script.strip()

                    for prefix in ["javascript", "js"]:
                        if cleaned_script.lower().startswith(prefix):
                            cleaned_script = cleaned_script[len(prefix):].lstrip(':').lstrip('\n').lstrip()

                    def is_truncated(s):
                        stack = []
                        pairs = {')': '(', '}': '{', ']': '['}
                        for c in s:
                            if c in '({[':
                                stack.append(c)
                            elif c in ')}]':
                                if not stack or stack[-1] != pairs[c]:
                                    return True
                                stack.pop()
                        return bool(stack)

                    chat_history.append({"role": "assistant", "content": cleaned_script})

                    if not cleaned_script:
                        value["exec"] = []

                    elif is_truncated(cleaned_script):
                        max_attempts = 7
                        attempts = 0
                        while is_truncated(cleaned_script) and attempts < max_attempts:
                            fixed_script = generate_script_v22_fix(cleaned_script, script_text, script_type)
                            new_script2 = fixed_script.strip()

                            for prefix in ["javascript", "js"]:
                                if new_script2.lower().startswith(prefix):
                                    new_script2 = new_script2[len(prefix):].lstrip(':').lstrip('\n').lstrip()

                            cleaned_script += new_script2
                            chat_history.append({"role": "assistant", "content": cleaned_script})
                            attempts += 1

                        if is_truncated(cleaned_script):
                            fixed_script = fix_syntax_v22(cleaned_script, script_text)
                            new_script3 = fixed_script.strip()

                            for prefix in ["javascript", "js"]:
                                if new_script3.lower().startswith(prefix):
                                    new_script3 = new_script3[len(prefix):].lstrip(':').lstrip('\n').lstrip()

                            cleaned_script += new_script3
                            chat_history.append({"role": "assistant", "content": cleaned_script})

                        value["exec"] = cleaned_script.splitlines() if cleaned_script else []
                    else:
                        value["exec"] = cleaned_script.splitlines()

                except Exception as e:
                    st.warning(f"Script conversion failed: {e}")

        if "item" in obj and isinstance(obj["item"], list):
            for subitem in obj["item"]:
                convert_scripts_in_collection(subitem)

        for key, value in obj.items():
            if key not in ("event", "script", "item"):
                convert_scripts_in_collection(value, parent_listen=parent_listen)

    elif isinstance(obj, list):
        for item in obj:
            convert_scripts_in_collection(item, parent_listen=parent_listen)


# ============================================================
# ===================== REFACTOR ENGINE =======================
# ============================================================

def get_event_script(item, listen_type):
    events = item.get("event", [])
    for ev in events:
        if ev.get("listen") == listen_type:
            exec_block = ev.get("script", {}).get("exec", [])
            if isinstance(exec_block, list):
                return "\n".join(exec_block)
            elif isinstance(exec_block, str):
                return exec_block
    return ""

def set_event_script(item, listen_type, script_text):
    if "event" not in item or not isinstance(item["event"], list):
        item["event"] = []

    for ev in item["event"]:
        if ev.get("listen") == listen_type:
            ev["script"] = {"type": "text/javascript", "exec": script_text.splitlines()}
            return

    item["event"].append({
        "listen": listen_type,
        "script": {"type": "text/javascript", "exec": script_text.splitlines()}
    })

def walk_requests(items, collected=None):
    if collected is None:
        collected = []
    for it in items:
        if "item" in it and isinstance(it["item"], list):
            walk_requests(it["item"], collected)
        else:
            if "request" in it:
                collected.append(it)
    return collected

def normalize_whitespace(text):
    return re.sub(r"[ \t]+", " ", text.strip())

def extract_pm_test_blocks(script):
    blocks = []
    i = 0
    n = len(script)

    while i < n:
        idx = script.find("pm.test", i)
        if idx == -1:
            break

        p_open = script.find("(", idx)
        if p_open == -1:
            break

        depth = 0
        j = p_open
        while j < n:
            if script[j] == "(":
                depth += 1
            elif script[j] == ")":
                depth -= 1
                if depth == 0:
                    end_idx = j + 1
                    if end_idx < n and script[end_idx] == ";":
                        end_idx += 1
                    chunk = script[idx:end_idx]
                    blocks.append(chunk.strip())
                    i = end_idx
                    break
            j += 1
        else:
            break

    return blocks

def remove_blocks_from_script(script, blocks_to_remove):
    new_script = script
    for blk in blocks_to_remove:
        pattern = re.escape(blk)
        new_script = re.sub(r"\n?\s*" + pattern + r"\s*\n?", "\n", new_script, count=1)

    new_script = re.sub(r"\n{3,}", "\n\n", new_script).strip()
    return new_script

def build_common_tests_function(common_blocks, fn_name="runCommonTests"):
    fn_lines = []
    fn_lines.append(f"function {fn_name}() {{")
    for blk in common_blocks:
        for line in blk.splitlines():
            fn_lines.append("  " + line)
        fn_lines.append("")
    fn_lines.append("}")
    return "\n".join(fn_lines).strip()

def ensure_common_function_in_collection_prerequest(collection, fn_text):
    existing = get_event_script(collection, "prerequest")

    if re.search(r"function\s+runCommonTests\s*\(", existing):
        # Replace existing runCommonTests (simple replace)
        existing = re.sub(
            r"function\s+runCommonTests\s*\([\s\S]*?\n\}",
            fn_text,
            existing,
            count=1
        )
        updated = existing.strip()
    else:
        updated = (existing.strip() + "\n\n" + fn_text).strip() if existing.strip() else fn_text.strip()

    set_event_script(collection, "prerequest", updated)

def inject_common_tests_call_into_request(script, fn_name="runCommonTests"):
    script = script.strip()
    call_line = f"{fn_name}();"

    if call_line in script:
        return script

    if not script:
        return call_line

    return call_line + "\n\n" + script

def refactor_collection_common_tests(collection_json):
    collection = deepcopy(collection_json)

    all_requests = walk_requests(collection.get("item", []))
    if not all_requests:
        return collection_json

    per_request_blocks = []
    for req_item in all_requests:
        test_script = get_event_script(req_item, "test")
        blocks = extract_pm_test_blocks(test_script)
        norm_blocks = [normalize_whitespace(b) for b in blocks]
        per_request_blocks.append(norm_blocks)

    common_set = None
    for blocks in per_request_blocks:
        s = set(blocks)
        if common_set is None:
            common_set = s
        else:
            common_set = common_set.intersection(s)

    common_blocks_norm = sorted(common_set) if common_set else []

    if not common_blocks_norm:
        return collection

    # Use first request script raw blocks as base for function
    first_test_script = get_event_script(all_requests[0], "test")
    first_raw_blocks = extract_pm_test_blocks(first_test_script)
    raw_map = {normalize_whitespace(b): b for b in first_raw_blocks}

    common_blocks_raw = []
    for nb in common_blocks_norm:
        common_blocks_raw.append(raw_map.get(nb, nb))

    fn_text = build_common_tests_function(common_blocks_raw, fn_name="runCommonTests")
    ensure_common_function_in_collection_prerequest(collection, fn_text)

    # Remove common blocks from each request
    for req_item in all_requests:
        original_script = get_event_script(req_item, "test")
        original_raw_blocks = extract_pm_test_blocks(original_script)

        remove_these = []
        for blk in original_raw_blocks:
            if normalize_whitespace(blk) in common_set:
                remove_these.append(blk)

        new_script = remove_blocks_from_script(original_script, remove_these)
        new_script = inject_common_tests_call_into_request(new_script, fn_name="runCommonTests")
        set_event_script(req_item, "test", new_script)

    return collection


# ============================================================
# ======================= MAIN RUNNER =========================
# ============================================================

if uploaded_zip:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "uploaded.zip")
        with open(zip_path, "wb") as f:
            f.write(uploaded_zip.read())

        extract_dir = os.path.join(tmpdir, "unzipped")
        os.makedirs(extract_dir, exist_ok=True)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_dir)

        st.success("Zip extracted. Starting script conversion...")

        converted_dir = os.path.join(tmpdir, "converted")
        os.makedirs(converted_dir, exist_ok=True)
        converted_files = 0

        total_files = sum(1 for root, _, files in os.walk(extract_dir) for file in files if file.endswith(".json"))
        progress_bar = st.progress(0)
        progress_text = st.empty()
        start_time = time.time()
        processed_files = 0

        for root, _, files in os.walk(extract_dir):
            for file in files:
                if file.endswith(".json"):
                    input_path = os.path.join(root, file)
                    try:
                        with open(input_path, "r", encoding="utf-8") as f:
                            raw = f.read()
                        collection_json = json.loads(raw)

                        if "item" not in collection_json:
                            st.warning(f"{file} skipped: No 'item' key found.")
                            processed_files += 1
                            continue

                        # 1) Convert scripts
                        convert_scripts_in_collection(collection_json)

                        # 2) Refactor common tests (optional)
                        if enable_refactor:
                            collection_json = refactor_collection_common_tests(collection_json)

                        # 3) Validate + save schema as v2.1 string
                        validate_as_v22_but_save_as_v21(collection_json)

                        out_path = os.path.join(converted_dir, f"{Path(file).stem}_converted_refactored.json")
                        with open(out_path, "w", encoding="utf-8") as f:
                            f.write(json.dumps(collection_json, indent=2))

                        converted_files += 1

                    except Exception as e:
                        st.error(f"Failed to process {file}: {e}")

                    processed_files += 1
                    elapsed = time.time() - start_time
                    avg_time = elapsed / processed_files if processed_files else 0
                    files_left = total_files - processed_files
                    est_time_left = int(avg_time * files_left)
                    mins, secs = divmod(est_time_left, 60)
                    progress = processed_files / total_files if total_files else 1
                    eta = datetime.now() + timedelta(seconds=est_time_left)
                    eta_str = eta.strftime('%H:%M:%S')
                    progress_bar.progress(progress)
                    progress_text.text(f"Processed {processed_files}/{total_files} files. Time left: {mins}m {secs}s. ETA: {eta_str}")
                    time.sleep(0.01)

        if converted_files == 0:
            st.warning("No valid .json files were converted.")
        else:
            valid_files = []
            invalid_files = []

            for file in os.listdir(converted_dir):
                file_path = os.path.join(converted_dir, file)
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    validate_as_v22_but_save_as_v21(data)
                    valid_files.append(file)

                except Exception as e:
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            raw_json = f.read()
                        fixed_json = generate_postman_v22_again(raw_json)
                        parsed = json.loads(fixed_json)
                        validate_as_v22_but_save_as_v21(parsed)
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(json.dumps(parsed, indent=2))
                        valid_files.append(file)
                        st.info(f"{file} was fixed and validated on retry.")
                    except Exception as e2:
                        invalid_files.append((file, f"Initial error: {e}; Retry error: {e2}"))

            if not valid_files:
                st.error("No valid collections were generated.")
                for fname, err in invalid_files:
                    st.info(f"{fname}: {err}")
            else:
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for file in valid_files:
                        file_path = os.path.join(converted_dir, file)
                        zipf.write(file_path, arcname=file)

                zip_buffer.seek(0)

                st.markdown("---")
                st.success(f"✅ Converted + Refactored {len(valid_files)} collection(s) successfully.")
                if invalid_files:
                    st.warning(f"⚠️ {len(invalid_files)} file(s) had issues.")
                    for fname, err in invalid_files:
                        st.info(f"{fname}: {err}")

                st.download_button(
                    label="Download Converted + Refactored Collections (.zip)",
                    data=zip_buffer,
                    file_name="converted_refactored_jsons.zip",
                    mime="application/zip"
                )
