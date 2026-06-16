## CONVERSION ENGINES:

1. PostmanConverter.py 
--> Main converter engine that converts old version postman scripts into new version scripts with some specified changes in the Test Result prints.
--> Used prompt - combination of the three prompts gptpriorityprompt.py, gptprompt1.py and old prompt.py
--> Uses .env and postman_collection_v2.2_schema.json for conversion.

2. PostmanConverter_ConvertAndRefactor.py
--> Converts like PostmanConverter.py as well as refactors the script.
--> Refactor: Puts all common and repeated pm.test(...) blocks in collection-level pre-request script inside a function definition. Removes those tests in each request Post-response script and adds the function call in them
--> Uses .env and postman_collection_v2.2_schema.json for conversion.

3. postmanConverter2.py
--> Finalized Postman converter script (enhanced grouping + collection-level)
 - Endpoint based grouping of common variables (substring matching)
 - Comments only request-level lines that match the collection-level validations
 - Inserts full funcUtils pre-request block (unmodified)
 - Inserts test script in requested format with generated arrays
 - Skips conversion for fully-commented scripts

## PROMPTS:

1. prompt
--> Simple and general conversion. No specified changes.

2. old prompt.py
--> Clear and Good, lengthy prompt with clear explanation. Conversion is good but assumes the example structure as actual structure and hence generates extra code

3. gptprompt1.py
--> Shorter prompt but not strong enough. Short sentences without clear explanation leads to undesired results

4. gptpriorityprompt.py
--> Strong with clear Rule definitions and Priorities for conversion.

###

I have combined the last three prompts with clear explanations and priorities with Rule definitions in PostmanConverter.py --> Conversion is more stronger.

## Changes made from Original code:

1. Request method + Request path + : + Test Name --> as prefix in all test prints
2. Setting raw api endpoint (with unresolved variables) as request_path in collection-level pre-request script - to add in each test prints
3. Status code, code and message --> as merged single test 
4. tv4.validate --> Ajv validation Rule added
