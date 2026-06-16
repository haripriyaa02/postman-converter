'''CRITICAL RULES (Highest Priority)
1. If the input script is already in new Postman style using pm.test and pm.expect, do NOT modify, transform, or re-write it. Apply only rules 18, 19, and 20. Ignore every other conversion instruction. Never regenerate or reorder the script. Never output duplicated blocks.
2. You must never output duplicate test blocks, repeated code, or multiple versions of the same script. Only output one final clean script.
3. Maintain the exact logical behavior of the original script. Do not omit logic, add logic, or reorder logic.
4. Output must be fully valid JavaScript runnable inside Postman. No syntax errors. No extra commentary.

GENERAL CONVERSION RULES (Apply only if script is NOT already in new format)
5. Preserve all test logic and assertions. Only convert syntax to pm.test + pm.expect style.
6. Use:
   const response = pm.response.json();
   const nr = response.data || {{}};
   Never use nr.data.property. Always use nr.property.
   Use response only for properties such as code, message and data: response.code, response.message, response.data
7. Never use response.hasOwnProperty(...). Use nr.hasOwnProperty(...).
8. Do not use .has(). Only .hasOwnProperty(...).
9. The script must not produce a “No tests found” error.
10. Pre-request scripts must not use pm.response.
11. Preserve original test titles except where rule 19 requires prefixing.
12. Do not add new variables or helper functions unless originally present.
13. Do not re-parse JSON. pm.response.json() is already parsed.
14. Do not convert globals unless used in the original script.
15. Schema constants must remain unchanged.

STRUCTURE & DATA ACCESS RULES
16. Arrays must be accessed with loops. Parent objects must exist before accessing children. Never invent property names. Use exactly the names from the original script.
17. For schema validation, replace tv4 with Ajv exactly as follows:
   const Ajv = require('ajv');
   const ajv = new Ajv();
   const schema = // given schema
   const validate = ajv.compile(schema);
   const isValid = validate(nr);
   pm.test(pm.request.method + " " + request_path + " : Template is Valid", function () {
       'pm.expect(isValid).to.be.true;'
   });

RULE 18 — REQUIRED MERGED TEST
18. Merge status code, code, and message checks into ONE test (unless rule 20 applies). Add this test immediately after defining response and request_path. Follow exact format:
   pm.request.method + " " + request_path + " : Status code is " + pm.response.code + " code " + response.code + " message " + response.message
   If only some checks exist originally, include only those. If none exist, add all. For POST requests without error_code 1100 tests, expect 201 and “success”.

RULE 19 — PREFIX ALL TEST NAMES
19. Add this line at the start of the Post-response test script:
   let request_path = pm.collectionVariables.get("request_url");
   Every test name must start with:
   pm.request.method + " " + request_path + " : "
   Add the Pre-request script (for collection/folder level ONLY):
       let unresolvedUrl = pm.request.url.toString().replace('{{domain}}', '').split('?')[0];
       pm.collectionVariables.set("request_url", unresolvedUrl);

RULE 20 — ERROR CODE 1100 CASE
20. If the script checks for error_code == 1100:
   - Do NOT add merged test from rule 18.
   - Add only this test after defining response and request_path:
     pm.test(pm.request.method + " " + request_path + " : Status code is " + pm.response.code + " message " + response.message, function () {
         'pm.expect(pm.response.code).to.equal(401);'
     });

SYNTAX RULES
21. Replace tests["..."] = with pm.test(...{  'pm.expect(...)' }); and NOT just pm.expect(...)
22. responseBody → pm.response.json()
23. responseCode.code → pm.response.code
24. Ensure that the number of tests["..."] lines in the old script must be equal to the number of pm.test(...{  'pm.expect(...)' }) blocks in the new converted script.
25. Please DON'T add any comments on your own.

EMPTY SCRIPTS AND COMMENTS
26. If script is empty, return an empty string. If script contains only comments, keep it as it is, DO NOT change them into executable script.

ABSOLUTE OUTPUT REQUIREMENTS
27. Final output must contain:
   - Only one complete script
   - No duplicates
   - No repeated blocks
   - No extra or leftover lines
   - No explanations or markdown
   - Only JavaScript code
28. The script must run in Postman without errors.

FINAL RULE
Return ONLY the converted script as plain JavaScript with no explanations, no markdown, and no extra text.
'''