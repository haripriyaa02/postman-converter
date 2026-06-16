#Old prompt (copy-pasted the function alone)

def generate_script_v22(old_script, script_type):
    prompt = f'''
You are an assistant that ONLY modernizes the syntax of a Postman {script_type} script.
Do NOT change any logic, do NOT add or infer tests, do NOT repeat blocks, do NOT restructure, 
do NOT access properties differently than the original script. Never guess JSON formats.

STRICT RULES:
1. KEEP THE LOGIC EXACTLY THE SAME.
2. DO NOT create duplicates of any block.
3. DO NOT introduce new if/else blocks.
4. DO NOT add new variable declarations.
5. DO NOT modify loops, conditions, or test order.
6. DO NOT add additional tests.
7. DO NOT rewrite test names.
8. DO NOT assume JSON structures that are not explicitly shown in the original code.
9. Only replace deprecated syntax with modern Postman syntax:
   - tests[...] → pm.test(...)
   - responseBody → pm.response.json()
   - responseCode.code → pm.response.code
   - pm.expect(...) MUST remain identical in logic.
10. DO NOT insert comments, examples, explanations, placeholders, or additional logic.
11. DO NOT expand or merge tests.
12. DO NOT add formatting, indentation, or beautification changes that alter execution.
13. If the script is already correct, return it unchanged.

ALLOWED CHANGES ONLY:
- Update syntax to new Postman style.
- Join multiline exec lists into valid JS.
- Remove comments if they exist.
- Fix syntax errors ONLY IF they already exist in the script
  (missing braces, missing parentheses, unfinished strings).

Return ONLY the converted JavaScript. No markdown. No explanation.

Original script:
---
{old_script}
---
'''
    payload = {
        "userprompt": prompt,
        "systemprompt": "",
        "model": "gpt-4.1-mini",
        "max_completion_tokens": 16000,
        "temperature": 0.0,
        "message": [{"role": "user", "content": prompt}]
    }
    response = requests.post(api_url, json=payload, timeout=1600)
    response.raise_for_status()
    return response.text.strip().strip('`\n"\' ')
