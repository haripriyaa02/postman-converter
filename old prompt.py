'''Instructions:
NOTE: If the scripts in the collection already exists in new format postman script (e.g., tests already in pm.test() blocks instead of tests[]), then DO NOT modify or add any extra lines to the script. Only follow instructions 18., 19. and 20. for the scripts of such collection and ignore all other instructions given below for conversion.
1. Preserve the original test logic and assertions, but make necessary structural changes if the data type requires it (e.g., accessing array elements with [i] when a property is an array in the response).
2. Understand the JSON structure from the older script and see how the properties are called and follow that same manner but with the new code.
3. If the script is empty, return an empty string and if there are comments in the script, remove them DO NOT change those commented lines to code.
4. Do not add extra sample code or usage examples and **DO NOT** use any placeholders for a property like property_name or the words "javascript" or "js" or add comments in between the code.
5. Return the full completed code with no syntax errors and same logic as the original script. If you see a const object which is a schema just retain it as it is.
6. When using `pm.response.json()`, assign it to a variable named `response`, and assign `response.data || {{}}` to a variable named `nr`. Do **not** try to access `nr.data.property`, instead use `nr.property` — `nr` itself is already the `data` section.
7. Never write `pm.expect(nr.data).to.have.property(...)` — that's incorrect. Use `pm.expect(nr).to.have.property(...)` instead. Also do not use `pm.expect(response.hasOwnProperty(...))` — use `pm.expect(nr.hasOwnProperty(...))` instead.
8. Keep in mind that there is no function like `pm.response.json(...).has()`. Use `.hasOwnProperty(...)` safely.
9. **DO NOT** give me a script which would lead to a "no tests found" error in Postman.
10. Do not use `pm.response` inside Pre-request scripts.
11. Preserve original test descriptions; do not reword test titles.
12. Do not add any new functions or variables unless they existed in the original test or pre-request script.
13. Do not use `JSON.parse(pm.response.json())` — `pm.response.json()` is already parsed.
14. Do not use `pm.globals.get(...)` in Pre-request scripts and do not use `pm.globals.set(...)` in Test scripts unless the original script used them.
15. If there is a schema which exists as a constant in the original script, do not make any changes to it. Just copy it as it is.
### Response structure:
Assume all scripts reference a JSON structure like this (from `pm.response.json()`) and use this JSON structure as the ground truth for typing and access logic:
{{
  "code": 0,
  "message": "success",
  "data": {{
    "summary_details": {{
      "down_count": 2,
      "downtime_duration": 120,
      "availability_percentage": 99.5,
      "mtbf": 300,
      "unmanaged_duration": 10,
      "alarm_count": 1,
      "mttr": 60,
      "maintenance_percentage": 0.5,
      "maintenance_duration": 30,
      "availability_duration": 7200,
      "unmanaged_percentage": 0.2,
      "downtime_percentage": 0.3,
      "critical_percentage": 0.1,
      "critical_count": 1,
      "critical_duration": 50,
      "trouble_percentage": 0.2,
      "trouble_count": 1,
      "trouble_duration": 70
    }},
    "charts": [
      {{
        "name": "Uptime Chart",
        "data_points": [...]
      }}
    ],
    "info": {{
      "report_name": "Top N Report",
      "report_type": 15,
      "limit": 10,
      "formatted_start_time": "2024-06-01 00:00:00",
      "formatted_end_time": "2024-06-30 23:59:59",
      "start_time": 1717200000000,
      "end_time": 1719791999000,
      "generated_time": 1719800000000,
      "formatted_generated_time": "2024-07-01 00:00:00",
      "timezone": "Asia/Kolkata",
      "period": "Last Month",
      "period_name": "June 2024",
      "monitor_type": "HOMEPAGE"
    }},
    "availability_details": [
      {{
        "monitor_id": 12345,
        "availability": 99.9
      }}
    ],
    "outage_details": [
      {{
        "monitor_id": 12345,
        "outages": [
          {{
            "outage_id": "out123",
            "start_time": 1717500000000,
            "end_time": 1717500600000,
            "duration": 60,
            "type": "critical"
          }}
        ]
      }}
    ],
    "profile_details": {{
      "profile_id": 987,
      "profile_name": "Critical Monitors"
    }},
    "performance_details": {{
      "HOMEPAGE": {{
        "name": "Homepage Load",
        "attribute_data": [...],
        "availability": [...],
        "tags": ["web", "latency"]
      }}
    }}
  }}
}}

16. Use the structure above to correctly navigate nested properties. For example:
    - Based on the given response structure, ensure all array-based properties like availability_details, charts, outage_details are safely looped or accessed with indices, even if the original script treated them like objects.
    - `data.summary_details.down_count` should be accessed via `nr.summary_details.down_count`
    - Never use `response.down_count` directly unless it is top-level (which it isn't in this example structure).
    - Always check if the parent (e.g., `summary_details`) exists before accessing its children.
    - If the property is an array (like `charts` or `outage_details`), use a `for` loop to iterate and check each element.
    - Keep in mind that the above structure is just an example given for you to understand how to access properties of response body from the script. 
    - Every script has different property names which will be used in the old script. Ensure that you use those names in the new converted script and not from this example structure.

17. To validate a schema, do not use tv4.validate. Use Ajv instead. Example usage:
    const Ajv = require('ajv');
    const ajv = new Ajv();
    const schema = //given schema
    const validate = ajv.compile(schema);
    const isValid = validate(nr); //replace nr with the variable they are validating in the old script
    pm.test(pm.request.method + " " + request_path + " : Template is Valid", function () {
            'pm.expect(isValid).to.be.true;'
        });

18. Merge all common basic tests: If separate pm.test or tests[..] blocks exist for: Status code is 200, code equals 0, message equals "success", merge them into one single pm.test.   
    - Test message format (exact pattern, the first part is default for all test messages):
      pm.request.method + " " + request_path + " : " + "Status code is " + pm.response.code + " code " + response.code + " message " + response.message
    - Example test name output:
      GET /api/example : Status code is 200 code 0 message success
    - The test should validate:
      pm.expect(pm.response.code).to.equal(200);
      pm.expect(response.code).to.eql(0);
      pm.expect(response.message).to.eql("success"); 
    - If pm.request.method is 'POST' and the script DOES NOT contain test for "error_code" to be 1100, then it must validate:
      pm.expect(pm.response.code).to.equal(201);
      pm.expect(response.message).to.eql("success"); 
    - If the test for any one of them is not present in the script, validate for the other two that are present. For example: The script has separate tests only for status code and message. Then the test will be like:
      'pm.test(pm.request.method + " " + request_path + " " + "Status code is " + pm.response.code + " message " + response.message, function() {
          'pm.expect(pm.response.code).to.equal(200);' 
          'pm.expect(response.message).to.eql("success");'
       });'
    - If the tests for status code, code and message does not exist in the script, write a new merged test for status code, code and message with the pattern given.
    - This must be done for every request's test script.
    - IMPORTANT: Please add this test after the variables response and request_path are defined.
    
19. Add a part with request method and request path and a colon before every test message.
    - Add the following script exactly and only to the collection level/folder level Pre-request script of the postman collection. DO NOT write this script in any Post-response test script.
        let unresolvedUrl = pm.request.url.toString().replace('{{domain}}', '').split('?')[0];
        pm.collectionVariables.set("request_url", unresolvedUrl); 
    - Add this line in the Post-response script of every request:
        let request_path = pm.collectionVariables.get("request_url");
    - Now add them in every test message like this: For example,
     If the test is : pm.test("Verify response structure", function...)
     Change the test name as : pm.test(pm.request.method + " " + request_path + " : " + "Verify response structure", function...)
    - This must be done for each and every test in every script of the collection.
20. If the test script contains test for "error_code" to be 1100, you should add the following test as the first test after the variables response and request_path are defined.
    No need to add the test that is given in instruction 18 to this type of requests. Add only the following test:
    pm.test(pm.request.method + " " + request_path + " : Status code is " + pm.response.code + " message " + response.message, function() {
    'pm.expect(pm.response.code).to.equal(401);'
    });




### Syntax changes:
- Replace `tests["..."] =` with `pm.test(...)`.
- Use `pm.expect(...)` instead of other assertion styles.
- Replace `responseBody` with `pm.response.json()`.
- Replace `responseCode.code` with `pm.response.code`.
- Replace all `postman.setGlobalVariable(...)` with `pm.globals.set(...)`.

### Global utilities:
- If a global function is stored using `postman.setGlobalVariable('function_name', ...)`, convert it as follows:
  - For Pre-request scripts:  
    `pm.globals.set(function_name, function_call() {{ ... }} + 'function_call()');`
  - For Test scripts:
    let function_call = pm.globals.get("function_name");
    eval(function_call);
    function_name();
    Ensure `function_call` and `function_name` are different strings to avoid name collision also always call the function_name after eval.

### Output:
Return the converted script **as plain JavaScript only**, with no additional comments, markdown, or explanation.
'''