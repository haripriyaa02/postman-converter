
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
from collections import Counter, defaultdict
from threading import Lock
AZURE_LOCK = Lock()
AZURE_SESSION = requests.Session()

adapter = requests.adapters.HTTPAdapter(
    pool_connections=1,
    pool_maxsize=1,
    max_retries=0
)

AZURE_SESSION.mount("https://", adapter)



# ---------------------------------------------------------------------
# Finalized Postman converter script (enhanced grouping + collection-level)
# - Endpoint based grouping of common variables (substring matching)
# - Comments only request-level lines that match the collection-level validations
# - Inserts full funcUtils pre-request block (unmodified)
# - Inserts test script in requested format with generated arrays
# - Skips conversion for fully-commented scripts
# ---------------------------------------------------------------------

chat_history = [
    {"role": "system", "content": "You are a Postman script conversion expert that follows specific conversion rules exactly. Never add extra code or comments."}
]

def split_script(script, max_lines=100):
    lines = script.splitlines()
    for i in range(0, len(lines), max_lines):
        yield "\n".join(lines[i:i+max_lines])
def classify_field_type(field):
    field = field.lower()
    if field in {"error_code", "error", "errors"}:
        return "error"
    return "success"


def validate_as_v22_but_save_as_v21(obj, schema):
    if "info" not in obj:
        obj["info"] = {}

    # validate using v2.2 schema
    obj["info"]["schema"] = "https://schema.getpostman.com/json/collection/v2.2.0/collection.json"
    validate(instance=obj, schema=schema)

    # save back as v2.1
    obj["info"]["schema"] = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"



# -------------------------- Log parsing / common detection --------------------------

def parse_newman_log_from_path(log_path):
    """
    Parse the Newman/Jenkins run log text file and extract validations per request.
    Returns dict: { request_name: { 'method': 'GET', 'url': '...', 'validations': set([...]) } }
    """
    per_request = {}
    if not log_path or not os.path.exists(log_path):
        return per_request

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = [l.rstrip("\n") for l in f]

    cur_req = None
    cur_method = None
    cur_url = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        # Request start line pattern: → <RequestName>
        if re.match(r'^[→↳]\s+', line):
            cur_req = re.sub(r'^[→↳]\s+', '', line).strip()
            # When a new request encountered, finalize previous if present
            if cur_req:
                per_request[cur_req] = {
                    "method": cur_method or "",
                    "url": cur_url or "",
                    "validations": set(per_request.get(cur_req, {}).get("validations", []))
                }
            
            cur_method = None
            cur_url = None
            validations = set()
            # Look ahead to find method+url and subsequent validation lines
            j = i + 1
            while j < len(lines):
                look = lines[j].strip()
                if look == "":
                    j += 1
                    continue
                # Method + URL line e.g., GET https://...
                m = re.match(r'^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+)', look)
                if m:
                    cur_method = m.group(1)
                    cur_url = m.group(2).split()[0] if m.group(2) else ""
                    j += 1
                    break
                # If next request starts, break
                if re.match(r'^[→↳]\s+', look):
                    break

                j += 1
            # gather validations after j until next '→'
            k = j
            while k < len(lines):
                l = lines[k].rstrip()
                s = l.strip()
                if re.match(r'^[→↳]\s+', s):
                    break

                # Successful validations lines often start with ✓
                # We capture the body after the check-mark or plain test titles
                if s.startswith("✓"):
                    token = s.lstrip("✓").strip()
                    # Normalize: remove leading/trailing punctuation and collapse spaces
                    token = re.sub(r'^\W+|\W+$', '', token)
                    token = re.sub(r'\s+', ' ', token)
                    if token:
                        validations.add(token)
                else:
                    # Some lines might be test names without checkmark; capture common patterns
                    if re.search(r'[A-Za-z0-9_\-\s]+', s):
                        possible = s.strip()
                        possible = re.sub(r'^\W+|\W+$', '', possible)
                        if possible and len(possible) > 2:
                            validations.add(possible)
                k += 1
            if not cur_req:
                cur_req = f"unknown_request_{i}"
            per_request[cur_req] = {
                "method": cur_method or "",
                "url": cur_url or "",
                "validations": validations
            }
            i = k
            continue
        i += 1
    return per_request




# -------------------------- Endpoint grouping + fuzzy token extraction --------------------------

EXCLUDE_GROUP_FIELDS = {
    "status",
    "code",
    "message",
    "and","or","array","object","has","have","required","validate","not","empty","first"
}





def extract_fields_from_log_validation(validation):
    if not validation:
        return set()

    quoted = re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"', validation)
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b', validation)

    tokens = {t.lower() for t in quoted + words}
    return {t for t in tokens if t not in EXCLUDE_GROUP_FIELDS}



# -------------------------- Commenting heuristics (updated to be endpoint-aware) --------------------------

def extract_collection_requests(collection):
    """
    Returns:
    {
      request_id: {
        "path": "/api/reports/outage/{{id}}",
        "segments": ("reports", "outage"),
        "script_exec": [...]
      }
    }
    """
    requests = {}

    def walk(obj, folder_path=()):
        if isinstance(obj, dict):
            if "request" in obj and isinstance(obj["request"], dict):
                url = obj["request"].get("url", {})
                raw = ""
                if isinstance(url, dict):
                    raw = url.get("raw", "")
                elif isinstance(url, str):
                    raw = url

                path = raw.split("?")[0]
                segments = tuple(
                    s for s in path.split("/") if s and not s.startswith("{{")
                )

                req_id = f"{obj.get('name','unknown')}|{path}"
                requests[req_id] = {
                    "path": path,
                    "segments": segments,
                    "obj": obj
                }

            if "item" in obj:
                for it in obj["item"]:
                    walk(it, folder_path)

    walk(collection)
    return requests

def cluster_requests_by_path(requests, min_shared=1):
    """
    Build clusters by choosing the deepest path prefix
    that is shared by at least `min_shared` requests.
    No hardcoded endpoint names.
    """

    # Step 1: collect all prefixes
    prefix_map = defaultdict(list)

    for reqid, info in requests.items():
        segs = info.get("segments", ())
        for depth in range(1, len(segs) + 1):
            prefix = segs[:depth]
            prefix_map[prefix].append(reqid)

    # Step 2: keep only prefixes shared by >= min_shared
    valid_prefixes = {
        prefix: reqs
        for prefix, reqs in prefix_map.items()
        if len(reqs) >= min_shared
    }

    # Step 3: for each request, find its deepest valid prefix
    best_clusters = defaultdict(list)

    for reqid, info in requests.items():
        segs = info.get("segments", ())
        best_prefix = None

        for depth in range(1, len(segs) + 1):
            prefix = segs[:depth]
            if prefix in valid_prefixes:
                best_prefix = prefix  # keep going deeper

        if best_prefix:
            best_clusters[best_prefix].append(reqid)

    return best_clusters



def extract_asserted_fields(line):
    """
    Extract asserted fields from BOTH active and commented assertions.
    """
    fields = set()

    # Normalize: remove leading comment markers safely
    cleaned = line.lstrip().lstrip("//").strip()

    # OLD style
    fields.update(re.findall(r'responseBody\.has\(\s*["\']([\w_]+)["\']\s*\)', cleaned))
    fields.update(re.findall(r'responseBody\.includes\(\s*["\']([\w_]+)["\']\s*\)', cleaned))
    fields.update(re.findall(r'tests\[\s*["\']([\w_]+)["\']\s*\]', cleaned))

    # NEW style
    fields.update(re.findall(r'\.to\.have\.property\(\s*["\']([\w_]+)["\']\s*\)', cleaned))
    fields.update(re.findall(r'hasOwnProperty\(\s*["\']([\w_]+)["\']\s*\)', cleaned))
    fields.update(re.findall(r'\.include\(\s*["\']([\w_]+)["\']\s*\)', cleaned))

    return {f.lower() for f in fields}
def map_common_validations_to_groups(
    per_request_log,
    asserted_fields_per_request,
    clusters,
    threshold=2,
):
    from collections import defaultdict

    # --------------------------------------------------
    # CACHES (performance only, no logic change)
    # --------------------------------------------------
    normalized_req = {r: normalize_name(r) for r in per_request_log}
    url_token_cache = {
        r: tokenize(info.get("url", ""))
        for r, info in per_request_log.items()
    }
    normalized_asserted = {
        normalize_name(r): fields
        for r, fields in asserted_fields_per_request.items()
    }

    # field -> set(request_name)
    field_usage = defaultdict(set)

    # --------------------------------------------------
    # PASS 1: Collect field → request usage
    # --------------------------------------------------
    for req_name, info in per_request_log.items():
        if normalized_req[req_name].startswith("cv"):
            continue

        validation_tokens = set()
        for v in info.get("validations", []):
            validation_tokens |= extract_fields_from_log_validation(v)

        log_tokens = validation_tokens | url_token_cache[req_name]

        script_fields = set()

        for rname_norm, fields in normalized_asserted.items():
            # Exact request-name match
            if rname_norm == normalized_req[req_name]:
                script_fields |= fields
                continue

            # Token-based loose match
            for tok in log_tokens:
                if tok in rname_norm:
                    script_fields |= fields
                    break

        for f in script_fields:
            if f not in EXCLUDE_GROUP_FIELDS:
                field_usage[f].add(req_name)

    # --------------------------------------------------
    # PASS 2: Field → BEST endpoint cluster
    # --------------------------------------------------
    groups = defaultdict(set)
    effective_threshold = max(2, threshold)

    for field, reqs in field_usage.items():
        if len(reqs) < effective_threshold:
            continue

        matched = set()

        for group_key, req_ids in clusters.items():
            clean_segments = [
                normalize_name(seg)
                for seg in group_key
                if seg not in ("api", "v1", "v2")
            ]

            for rid in req_ids:
                rname, _ = rid.split("|", 1)
                rname_norm = normalize_name(rname)

                for r in reqs:
                    # Exact request-name match
                    if normalized_req[r] == rname_norm:
                        matched.add(group_key)
                        break

                    # Depth-aware path match
                    url_tokens = url_token_cache[r]
                    match_count = sum(seg in url_tokens for seg in clean_segments)
                    if match_count >= max(1, len(clean_segments) - 1):
                        matched.add(group_key)
                        break

        if not matched:
            for r in reqs:
                groups[(normalized_req[r],)].add(field)
        elif len(matched) == 1:
            groups[next(iter(matched))].add(field)
        else:
            groups[max(matched, key=len)].add(field)

    # global_fields intentionally empty (no safe inference)
    global_fields = set()
    return groups, global_fields




# -------------------------- Collection-level script generation (new) --------------------------

# The full funcUtils block (pre-request). Insert your exact block here (kept as given).
# NOTE: Keep this block unmodified except for ensuring it's a list of lines for JSON exec.
# FUNCUTILSBLOCK = """pm.globals.set('funcUtils', function() {
#   function loadFuncUtils() { return 'availabilityScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null && Object.keys(jsonData.data).length === 5) {


#                     //Summary details validation
#                     var summaryJson = jsonData.data.summary_details;
#                     var length = summaryAssertConst.length;
#                     pm.test("summary_details is present", function () { pm.expect(jsonData.data).to.have.property("summary_details") && pm.expect(summaryJson).not.null });
#                     if (summaryJson != null) {
#                         let summaryBool = false;
#                        summaryAssertConst.forEach(key => {
#                                 if(!summaryJson.hasOwnProperty(key)){
#                                   assertkey = key;
#                                   summaryBool =false;
#                                   console.log("summary_details details missing attributes -" + key +" for " + reqestPAth);
#                                  }
#                                 else{
#                                     summaryBool=true;
#                                 }    
#                             });
#                                    pm.test("summary_details details contains all attributes for " + reqestPAth, function (){pm.expect(summaryBool).to.be.true && pm.expect(assertkey).to.be.null});
#                                    assertkey = null;
#                     }

#                 //To find extra key in response
#                 if(length<=Object.keys(summaryJson).length){
#                  Object.keys(summaryJson).forEach(key=>{
#                    if(!summaryAssertConst.includes(key)){
#                    console.log("summary details has extra key "+ key + "for" + reqestPAth);
#                    }
#                  });
#                 }

#                     //charts
#                     chartJson = jsonData.data.charts;
#                     pm.test("Charts is present", function(){pm.expect(jsonData.data).to.have.property("charts") && pm.expect(chartJson).not.null});
#                     if(chartJson!= null && chartJson.length>0){
#                         let chartBool =false;
#                         for(var i=0;i<chartJson.length;i++){
#                        chartAssertConst.forEach(key => {
#                                 if(chartJson[i].hasOwnProperty(key)){
#                                   chartBool =true;
#                                  }
#                                 else{
#                                     assertkey = key;
#                                     chartBool=false;
#                                   console.log("Charts details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });                           
#                         }
#                         pm.test("Chart details contains all attributes  for " + reqestPAth, function (){pm.expect(chartBool).to.be.true  && pm.expect(assertkey).to.be.null});
#                         assertkey = null;
#                     }                
#                 //To find extra key in response
#                 if(chartAssertConst.length<=Object.keys(chartJson).length){
#                  Object.keys(chartJson).forEach(key=>{
#                    if(!chartAssertConst.includes(key)){
#                     console.log("chart has extra key " +key+" for "+ reqestPAth);
#                    }
#                  });
#                 }

#                        //outage_details
#                        //execute only when percentage != 100
#                        if(summaryJson.availability_percentage<100){
#                         var outageJson = jsonData.data.outage_details;
#                         pm.test("Outage details is present", function () { pm.expect(jsonData.data).to.have.property("outage_details") && pm.expect(outageJson.length)>0});
#                         if (jsonData.data.outage_details != null && jsonData.data.outage_details.length>0) {
#                         let outageBool =false;
#                         for (let i = 0; i < outageJson.length; i++) {
#                         outageDetAssertConst.forEach(key => {
#                                 if(outageJson[i].hasOwnProperty(key)){
#                                     outageBool =true;
#                                     if(outageJson[i].outages!= null && outageJson[i].outages.length>0){
#                                         // for (let j = 0; j < outageJson[i].outages.length; j++) {
#                                         var outages = outageJson[i].outages[0];
#                                          outagesAssesrtConst.forEach(key => {
#                                              if(!outages.hasOwnProperty(key)){
#                                              pm.test("outages missing attributes -" + key +" for " + reqestPAth, function (){pm.expect(outages).to.have.property(key)});
#                                              }
#                                          });
                                        
#                                     }
#                                  }
#                                 else{
#                                     assertkey = key;
#                                     outageBool=false;
#                                     console.log("outage details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });   
                                    
#                         }
#                     pm.test("outage details contains all attributes  for " + reqestPAth, function (){pm.expect(outageBool).to.be.true  && pm.expect(assertkey).to.be.null});
#                     assertkey=null;
#                     } //To find extra key in response
#                  if(outageDetAssertConst.length<=Object.keys(outageJson).length){
#                  Object.keys(outageJson[0]).forEach(key=>{
#                    if(!outageDetAssertConst.includes(key)){
#                      console.log("outage_details has extra key "+ key +reqestPAth);
#                    }
#                  });
#                 }   
#                     } 
    

#                     // availability_details
#                     var availabilityJson = jsonData.data.availability_details;
#                     pm.test("Availability details is present ", function () { pm.expect(jsonData.data).to.have.property("availability_details") && pm.expect(availabilityJson.length)>0});
#                     if (availabilityJson.length > 0 && availabilityJson != null) {
#                         var availBool=false;
#                         for (let i = 0; i< availabilityJson.length; i++) {
#                             var downdetBool  = false;
#                             let monitorId = availabilityJson[i].monitor_id;
#                             availDetailAssertConst.forEach(key => {
#                                 if(availabilityJson[i].hasOwnProperty(key)){
#                                     availBool =true;
#                                     if(availabilityJson[i].down_details!= null && availabilityJson[i].down_details.length>0 && availabilityJson[i].availability_percentage!=100.0 ){
#                                         for (let j = 0; j < availabilityJson[i].down_details.length; j++) {
#                                         var downdet = availabilityJson[i].down_details[0];
#                                          downDetConst.forEach(key => {
#                                            if(!downdet.hasOwnProperty(key) && key!="actual_outage_time"){
#                                              pm.test("down_details missing attributes -" + key +" for monitor_id = "+ monitorId , function (){
#                                                 pm.expect(downdet).to.have.property(key)});
#                                              }else if(key=="actual_outage_time"){
#                                                 downdetBool =true;
#                                              }
#                                          });
#                                          if(j=20){
#                                             break;
#                                          }
#                                     }
#                                  }
#                                 }
#                                 else{
#                                     assertkey = key;
#                                     availBool=false;
#                                     console.log("Availability details " +[i]+" missing attributes -" + key +" for monitor_id = "+ monitorId);
#                                     // pm.test("Availability details " +[i]+" contains actual_outage_time for monitor_id = "+ monitorId , function (){
#                                     //     pm.expect(downdetBool).to.be.true});                           
#                                 }
#                             });
#                             if(downdetBool){
#                              console.log("down_details contains actual_outage_time for monitor id " + monitorId);
#                             } 
#                         }
#                         pm.test("Availability details contains all attributes for " + reqestPAth , function (){pm.expect(availBool).to.be.true && pm.expect(assertkey).to.be.null});
#                         assertkey=null;
#                         }   
#                          //To find extra key in response
#                   if(availDetailAssertConst.length<=Object.keys(availabilityJson).length){
#                  Object.keys(availabilityJson[0]).forEach(key=>{
#                    if(!availDetailAssertConst.includes(key) && key!="tags"){
#                      console.log("availability_details has extra key "+key+ " for " +reqestPAth);
#                    }
#                  });
#                 }                   
              
#                     //info                                                    
#                     var infoJson = jsonData.data.info;
#                     pm.test("info is present", function () { pm.expect(jsonData.data).to.have.property("info") && pm.expect(infoJson).not.null });
#                     if (infoJson != null) {
#                         let infoBool =false;
#                       filteredInfoArrayAvail.forEach(key => {
#                                 if(infoJson.hasOwnProperty(key)){
#                                   infoBool =true;
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     infoBool=false;
#                                   console.log("info details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });
#                                    pm.test("info details contains all attributes for " + reqestPAth, function (){pm.expect(assertkey).to.be.null && pm.expect(infoBool).to.be.true});   
#                                    assertkey=null;     
#                     }                
#                 //To find extra key in response
#                  if(filteredInfoArrayAvail.length<=Object.keys(infoJson).length){
#                  Object.keys(infoJson).forEach(key=>{
#                    if(!filteredInfoArrayAvail.includes(key) && key!="segment_type_name" && key!="segment_type"){
#                    console.log("info has extra key "+key+" for "+reqestPAth);
#                    }
#                  });
#                 }
                        
# }
# }
# },
#      'summaryScriptExe': (params) => {
#        var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null && Object.keys(jsonData.data).length === 5) {

#                     //Summary details validation
#                     var summaryJson = jsonData.data.summary_details;
#                     pm.test("summary_details is present", function () { pm.expect(jsonData.data).to.have.property("summary_details") && pm.expect(summaryJson).not.null });
#                     if (summaryJson != null) {
#                         let summaryBool =false;
#                        summaryAssertConst.forEach(key => {
#                                 if(summaryJson.hasOwnProperty(key)){
#                                   summaryBool =true;
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     summaryBool=false;
#                                   console.log("summary_details details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });
#                                    pm.test("summary_details details contains all attributes for " + reqestPAth, function (){pm.expect(summaryBool).to.be.true  && pm.expect(assertkey).to.be.null});
#                             assertkey = null;
#                     }
#                 //To find extra key in response
#                  if(summaryAssertConst.length<=Object.keys(summaryJson).length){
#                  Object.keys(summaryJson).forEach(key=>{
#                    if(!summaryAssertConst.includes(key)){
#                      console.log("summary_details has extra key " +key+ " for "+ reqestPAth);
#                    }
#                  });
#                 }

#                     // outage_details
#                     //execute only when percentage != 100
#                        if(summaryJson.availability_percentage<100){
#                         var outageJson = jsonData.data.outage_details;
#                         pm.test("Outage details is present", function () { pm.expect(jsonData.data).to.have.property("outage_details") && pm.expect(outageJson.length)>0});
#                         if (jsonData.data.outage_details != null && jsonData.data.outage_details.length>0) {
#                         let outageBool =false;
#                         for (let i = 0; i < outageJson.length; i++) {
#                         outageDetAssertConst.forEach(key => {
#                                 if(outageJson[i].hasOwnProperty(key)){
#                                     outageBool =true;
#                                     if(outageJson[i].outages[0]!= null && outageJson[i].outages.length>0){
#                                          var outages = outageJson[i].outages[0];
#                                          outagesAssesrtConst.forEach(key => {
#                                              if(!outages.hasOwnProperty(key)){
#                                             console.log("outages missing attributes -" + key +" for " + reqestPAth, function (){pm.expect(outages).to.have.property(key)});
#                                              }
#                                          });
#                                     }
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     outageBool=false;
#                                    console.log("outage details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });
#                 //To find extra key in response
#                  if(outageDetAssertConst.length<=Object.keys(outageJson).length){
#                  Object.keys(outageJson[0]).forEach(key=>{
#                    if(!outageDetAssertConst.includes(key)){
#                     console.log("outage_details has extra key "+key+" for" + reqestPAth);
#                    }
#                  });
#                  }                           
#                         }
#                     pm.test("outage details contains all attributes  for " + reqestPAth, function (){pm.expect(outageBool).to.be.true && pm.expect(assertkey).to.be.null});
#                     assertkey=null
#                     }
#                     }
 
                    
#                     //availability_details
#                     var availabilityJson = jsonData.data.availability_details;
#                     pm.test("Availability details is present ", function () { pm.expect(jsonData.data).to.have.property("availability_details") && pm.expect(availabilityJson.length)>0});
#                     if (availabilityJson.length > 0 && availabilityJson != null) {
#                         var availBool=false;
#                         for (let i = 0; i< availabilityJson.length; i++) {
#                             var downdetBool  = false;
#                             let monitorId = availabilityJson[i].monitor_id;
#                             availDetailAssertConst.forEach(key => {
#                                 if(availabilityJson[i].hasOwnProperty(key)){
#                                     availBool =true;
#                                     if(availabilityJson[i].down_details!= null && availabilityJson[i].down_details.length>0 && availabilityJson[i].availability_percentage!=100.0 ){
#                                         for (let j = 0; j < availabilityJson[i].down_details.length; j++) {
#                                         var downdet = availabilityJson[i].down_details[0];
#                                          downDetConst.forEach(key => {
#                                            if(!downdet.hasOwnProperty(key) && key!="actual_outage_time"){
#                                              pm.test("down_details missing attributes -" + key +" for monitor_id = "+ monitorId , function (){
#                                                 pm.expect(downdet).to.have.property(key)});
#                                              }else if(key=="actual_outage_time"){
#                                                 downdetBool =true;
#                                              }
#                                          });
#                                          if(j=20){
#                                             break;
#                                          }
#                                     }
#                                  }
#                                 }
#                                 else{
#                                     assertkey =key;
#                                     availBool=false;
#                                    console.log("Availability details " +[i]+" missing attributes -" + key +" for monitor_id = "+ monitorId );
#                                     // pm.test("Availability details " +[i]+" contains actual_outage_time for monitor_id = "+ monitorId , function (){
#                                     //     pm.expect(downdetBool).to.be.true });   
#                                 }
#                             });
#                             if(downdetBool){
#                              console.log("down_details contains actual_outage_time" )
#                             }    
#                 //To find extra key in response
#                  if(availDetailAssertConst.length<=Object.keys(availabilityJson[0]).length){
#                  Object.keys(availabilityJson[0]).forEach(key=>{
#                    if(!availDetailAssertConst.includes(key)){
#                      console.log("availability_details has extra key "+ key + " for " + reqestPAth);
#                    }
#                  });
#                  } 
                        
#                         }
#                     pm.test("Availability details contains all attributes for " + reqestPAth , function (){pm.expect(availBool).to.be.true  && pm.expect(assertkey).to.be.null});
#                     assertkey = null;
#                     }                
               
  
#                     //info
#                     var infoJson = jsonData.data.info;
#                     pm.test("info is present", function () { pm.expect(jsonData.data).to.have.property("info") && pm.expect(infoJson).not.null });
#                     if (infoJson != null) {
#                         let infoBool =false;
#                        filteredInfoArray.forEach(key => {
#                                 if(infoJson.hasOwnProperty(key)){
#                                   infoBool =true;
#                                  }
#                                 else{
#                                     assertkey =key;
#                                     infoBool=false;
#                                   console.log("info details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });
#                                    pm.test("info details contains all attributes for " + reqestPAth, function (){pm.expect(infoBool).to.be.true && pm.expect(assertkey).to.be.null});

#                             assertkey = null;
#                     }     
#                //To find extra key in response
#                  if(filteredInfoArray.length<=Object.keys(infoJson).length){
#                  Object.keys(infoJson).forEach(key=>{
#                    if(!filteredInfoArray.includes(key)){
#                     console.log("info has extra key "+ key + reqestPAth);
#                    }
#                  });
#                  }

#                     //performance details
#                         var performanceJson = jsonData.data.performance_details;
#                         pm.test("performance_details is present", function () { pm.expect(jsonData.data).to.have.property("performance_details") && pm.expect(performanceJson.length)>0});
#                         if (jsonData.data.performance_details != null && Object.keys(performanceJson).length>0) {
#                            let performanceBool =false;
#                             for (var singleJSONkey in performanceJson){
#                                 var singleJSON =performanceJson[singleJSONkey]
#                            perfAssertconst.forEach(key => {
#                                 if(singleJSON[key]!=null && key!="monitor_id_list"){
#                                     performanceBool =true;
#                                  }
#                                  else if(key=="monitor_id_list"){
#                                   console.log("monitor_id_list is found");
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     performanceBool=false;
#                                   console.log("performance details missing attributes -" + key +" for " + singleJSONkey);
#                                 }    
#                             }); 
#                             }
#                     pm.test("Performance details contains all attributes  for " + reqestPAth, function (){pm.expect(performanceBool).to.be.true && pm.expect(assertkey).to.be.null});
#                     assertkey = null;
#                     }                
#                     } 
#                  }
#     },
#     'performanceScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null) {
#             //Group_data
#                         var groupJson = jsonData.data.group_data;
#                         pm.test("group_data is present", function () { pm.expect(jsonData.data).to.have.property("group_data") && pm.expect(groupJson.length)>0});
#                         if (jsonData.data.group_data != null && Object.keys(groupJson).length>0) {
#                            let performanceBool =false;
#                             for (var singleJSONkey in groupJson){
#                                 var singleJSON =groupJson[singleJSONkey]
#                            groupdataAssertConst.forEach(key => {
#                                 if(singleJSON[key]!=null && key!="monitor_id_list"){
#                                     performanceBool =true;
#                                  }
#                                  else if(key=="monitor_id_list"){
#                                   console.log("monitor_id_list is found");
#                                  }
#                                 else{
#                                     assertkey =key;
#                                     performanceBool=false;
#                                     console.log("group_data details missing attributes -" + key +" for " + singleJSONkey);
#                                 }    
#                             }); 
#                             }
#                     pm.test("group_data details contains all attributes  for " + reqestPAth, function (){pm.expect(performanceBool).to.be.true && pm.expect(assertkey).to.be.null});
#                     assertkey=null;
#                     }

#                  //info
#                     var infoJson = jsonData.data.info;
#                     pm.test("info is present", function () { pm.expect(jsonData.data).to.have.property("info") && pm.expect(infoJson).not.null });
#                     if (infoJson != null) {
#                         let infoBool =false;
#                        infoAssertionPerfConst.forEach(key => {
#                                 if(infoJson.hasOwnProperty(key)){
#                                   infoBool =true;
#                                  }
#                                 else{
#                                     assertkey = key;
#                                     infoBool=false;
#                                    pm.test("info details missing attributes -" + key +" for " + reqestPAth, function (){pm.expect(infoJson).to.have.property(key)});
#                                 }    
#                             });
#                                    pm.test("info details contains all attributes for " + reqestPAth, function (){pm.expect(infoBool).to.be.true  && pm.expect(assertkey).to.be.null});

#                             assertkey =null;
#             }               
#              //To find extra key in response
#                  if(infoJson.length<=Object.keys(infoAssertionPerfConst).length){
#                  Object.keys(infoJson).forEach(key=>{
#                    if(!infoAssertionPerfConst.includes(key)){
#                      console.log("summary_details has extra key "+ key + reqestPAth);
#                    }
#                  });}
#                 }
#                 }
#     },
#     'trendScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null) {
#                     //trend
#                       trendJson = jsonData.data.trend;
#                     pm.test("Trend details is present", function(){pm.expect(jsonData.data).to.have.property("trend") && pm.expect(trendJson).not.null});
#                     if(trendJson!= null && trendJson.length>0){
#                         let trendBool =false;
#                         for(var i=0;i<trendJson.length;i++){
#                        trendAssertConst.forEach(key => {
#                                 if(trendJson[i].hasOwnProperty(key)){
#                                   trendBool =true;
#                                  }
#                                 else{
#                                     assertkey = key ;
#                                     trendBool=false;
#                                   console.log("Trend details missing attributes -" + key +" for " + reqestPAth);
#                                 }    
#                             });                           
#                         }
#                         pm.test("Trend details contains all attributes  for " + reqestPAth, function (){pm.expect(trendBool).to.be.true && pm.expect(assertkey).to.be.null});
#                         assertkey = null;
#                     }

#                     //info
#                     var infoJson = jsonData.data.info;
#                     pm.test("info is present", function () { pm.expect(jsonData.data).to.have.property("info") && pm.expect(infoJson).not.null });
#                     if (infoJson != null) {
#                         let infoBool =false;
#                        infoAssertionConst.forEach(key => {
#                                 if(infoJson.hasOwnProperty(key)){
#                                    infoBool =true;
#                                  }
#                                 else{
#                                     assertkey =key;
#                                    infoBool=false;
#                                    console.log("info details status details "+ i +" missing attributes -" + key +" for  path - " + reqestPAth);
#                                 //    pm.test("info details missing attributes -" + key +" for " + reqestPAth, function (){pm.expect(infoJson).to.have.property(key)});
#                                 }    
#                             });
#                             pm.test("info details contains all attributes for " + reqestPAth, function (){pm.expect(infoBool).to.be.true && pm.expect(assertkey).to.be.null});
#                             assertkey =null;
#                      }
#                 }
#                 }
#     },
#     'monStatusScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null) {

#                     //details
#                     var detailsJson = jsonData.data.details;
#                     let msbool =false;
#                     pm.test("monitor status details is present", function () { pm.expect(jsonData.data).to.have.property("details") && pm.expect(detailsJson).not.null });
#                     for(i=0;i<detailsJson.length;i++){
#                         var monitorID = detailsJson[i].Monitorid;
#                     if (detailsJson[i] != null) {
#                        monstatusAssertConst.forEach(key => {
#                                 if(detailsJson[i].hasOwnProperty(key)){
#                                   msbool =true;
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     msbool=false;
#                                    console.log("monitor status details "+ i +" missing attributes -" + key +" for  monitor ID - " + monitorID);
#                                 }    
#                             });
#                       }
#                       }           
#                             pm.test("monitor status details contains all attributes for " + reqestPAth, function (){pm.expect(msbool).to.be.true && pm.expect(assertkey).to.be.null});
#                      assertkey =null;
#                 }
#                 }
#     },
#         'topNScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null) {

#                     //Report
#                     var reportJson = jsonData.data.report;
#                     let topNbool =false;
#                     pm.test("TopN report is present", function () { pm.expect(jsonData.data).to.have.property("report") && pm.expect(reportJson).not.null });
#                     for(i=0;i<reportJson.length;i++){
#                         var monitorID = reportJson[i].monitor_id;
#                     if (reportJson[i] != null) {
#                        topNReportAssertConst.forEach(key => {
#                                 if(reportJson[i].hasOwnProperty(key)){
#                                   topNbool =true;
#                                  }
#                                 else{
#                                     assertkey=key;
#                                     topNbool=false;
#                                    console.log("TopN report "+ i +" missing attributes -" + key +" for  monitor ID - " + monitorID);
#                                 }    
#                             });
#                       }
#                       }           
#                             pm.test("TopN report contains all attributes for " + reqestPAth, function (){pm.expect(topNbool).to.be.true && pm.expect(assertkey).to.be.null});
#                      assertkey =null;

#                     //info
#                     var infoJson = jsonData.data.info;
#                     pm.test("info is present", function () { pm.expect(jsonData.data).to.have.property("info") && pm.expect(infoJson).not.null });
#                     if (infoJson != null) {
#                         let infoBool =false;
#                        topNInfoAssertConst.forEach(key => {
#                                 if(infoJson.hasOwnProperty(key)){
#                                    infoBool =true;
#                                  }
#                                 else{
#                                     assertkey =key;
#                                    infoBool=false;
#                                    console.log("info details status details "+ i +" missing attributes -" + key +" for  path - " + reqestPAth);
#                                 //    pm.test("info details missing attributes -" + key +" for " + reqestPAth, function (){pm.expect(infoJson).to.have.property(key)});
#                                 }    
#                             });
#                             pm.test("info details contains all attributes for " + reqestPAth, function (){pm.expect(infoBool).to.be.true && pm.expect(assertkey).to.be.null});
#                             assertkey =null;
#                      }
#                 }
#                 }
#     },
#         'forecastScriptExe': (params) => {
#             var assertkey = null;
#             var jsonData = pm.response.json();
#             var reqestTitle  = pm.info.requestName;
#             var reqestPAth = pm.request.url.getPath();
#             // pm.test("Status code is 200  & code 0  & message success  --" + reqestTitle, function () { pm.response.to.have.status(200) });

#             //data
#             if (pm.response.code == 200 && jsonData.code == 0) {
#                 pm.test("results contains field data for "+ reqestPAth, function () { pm.expect(jsonData).to.have.property("data") && pm.expect(jsonData.data).not.to.empty });
#                 if (jsonData.data != null) {

                 
#                 }
#                 }
#     }
# };
# } + ' loadFuncUtils()');}
  
  
# });"""

# For safety: split the block into lines for exec insertion
# def funcutils_block_lines():
#     # produce a clean list of strings for exec
#     return [ln for ln in FUNCUTILS_BLOCK.splitlines()]
def generate_collection_level_scripts_from_common(groups):

    # -------------------- Pre-request --------------------
    prereq_lines = [
        "let unresolvedUrl = pm.request.url.toString().replace('{{domain}}','').split('?')[0];",
        "pm.collectionVariables.set('request_url', unresolvedUrl);",
    ]

    # -------------------- Common header --------------------
    test_lines = [
        "let request_path = pm.collectionVariables.get('request_url') || pm.request.url.getPath();",
        "let response = {};",
        "try { response = pm.response.json(); } catch (e) { response = {}; }",
        "let nr = response.data || {};",
        "",
        "const isUnauthorized = pm.response.code === 401;",
        "const isNotFound = pm.response.code === 404;",
        "",
        "if (isUnauthorized) {",
        "  pm.test(pm.request.method + ' ' + request_path + ' : Status code is 401', function () {",
        "    pm.expect(pm.response.code).to.equal(401);",
        "  });",
        "  return;",
        "} else if (isNotFound) {",
        "  pm.test(pm.request.method + ' ' + request_path + ' : Status code is 404', function () {",
        "    pm.expect(pm.response.code).to.equal(404);",
        "  });",
        "  return;",
        "} else {",
        "  pm.test(pm.request.method + ' ' + request_path + ' : Status code is 200 code 0 message success', function () {",
        "    if (pm.request.method === 'POST') pm.expect(pm.response.code).to.equal(201);",
        "    else pm.expect(pm.response.code).to.equal(200);",
        "    if (response.code !== undefined) pm.expect(response.code).to.eql(0);",
        "    if (response.message !== undefined) pm.expect(response.message).to.eql('success');",
        "  });",
        "}",
        "",
        "// -------------------- PATH-AWARE FIELD FINDER --------------------",
        "function findFieldPath(obj, field, basePath) {",
        "  if (obj === null || obj === undefined) return null;",
        "",
        "  if (typeof obj === 'object' && !Array.isArray(obj)) {",
        "    if (Object.prototype.hasOwnProperty.call(obj, field)) {",
        "      return basePath + '.' + field;",
        "    }",
        "  }",
        "",
        "  if (Array.isArray(obj)) {",
        "    for (let i = 0; i < obj.length; i++) {",
        "      const res = findFieldPath(obj[i], field, basePath + '[' + i + ']');",
        "      if (res) return res;",
        "    }",
        "  } else if (typeof obj === 'object') {",
        "    for (let key in obj) {",
        "      if (!Object.prototype.hasOwnProperty.call(obj, key)) continue;",
        "      const res = findFieldPath(obj[key], field, basePath + '.' + key);",
        "      if (res) return res;",
        "    }",
        "  }",
        "",
        "  return null;",
        "}",
        "",
    ]

    # -------------------- BUILD ASSERT ARRAY --------------------
    test_lines.append("const ASSERTS = [")

    for group_key, fields in groups.items():
        if not fields:
            continue

        clean_tokens = [seg for seg in group_key if seg not in ("api", "v1", "v2")]
        if not clean_tokens:
            continue

        endpoint = "/" + "/".join(clean_tokens)
        js_fields = ", ".join(f"'{f}'" for f in sorted(fields))

        test_lines.extend([
            "  {",
            f"    urlContains: '{endpoint}',",
            f"    fields: [{js_fields}]",
            "  },",
        ])

    test_lines.append("];")
    test_lines.append("")

    # -------------------- Assertion execution (PATH-AWARE) --------------------
    test_lines.extend([
        "if (request_path.includes('api')) {",
        "  const testedFields = new Set();",
        "",
        "  ASSERTS.forEach(assertBlock => {",
        "    if (!request_path.includes(assertBlock.urlContains)) return;",
        "",
        "    assertBlock.fields.forEach(field => {",
        "      if (testedFields.has(field)) return;",
        "",
        "      const foundPath = findFieldPath(nr, field, 'data');",
        "      if (!foundPath) return;",
        "",
        "      testedFields.add(field);",
        "",
        "      pm.test(",
        "        pm.request.method + ' ' + request_path + ' : ' + foundPath + ' present',",
        "        function () {",
        "          pm.expect(foundPath).to.not.be.null;",
        "        }",
        "      );",
        "    });",
        "  });",
        "}",
    ])

    return prereq_lines, test_lines

MAX_CHUNK = 6000

def split_large_script(script):
    lines = script.splitlines()
    chunks, current = [], []
    size = 0

    for line in lines:
        size += len(line)
        current.append(line)
        if size >= MAX_CHUNK:
            chunks.append("\n".join(current))
            current, size = [], 0

    if current:
        chunks.append("\n".join(current))
    return chunks

def collect_asserted_fields_per_request_raw(collection):
    """
    Collect asserted fields BEFORE conversion (legacy + new style).
    """
    result = {}

    def walk(o):
        if isinstance(o, dict):
            if "request" in o and "event" in o:
                name = o.get("name")
                fields = set()

                for ev in o.get("event", []):
                    if ev.get("listen") == "test":
                        for line in ev.get("script", {}).get("exec", []):
                            fields |= extract_asserted_fields(line)

                if name and fields:
                    result[name] = fields

            for v in o.values():
                walk(v)

        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(collection)
    return result



def is_status_only_block(lines):
    joined = " ".join(lines).lower()
    return (
        ("pm.response.code" in joined or "response.code" in joined)
        and "pm.expect(nr." not in joined
        and "hasownproperty" not in joined
    )


def collect_asserted_fields_per_request(collection):
    """
    Returns:
    {
      request_name: set(asserted_fields)
    }
    """
    result = {}

    def walk(o):
        if isinstance(o, dict):
            if "request" in o and "event" in o:
                name = o.get("name")
                fields = set()

                for ev in o.get("event", []):
                    if ev.get("listen") == "test":
                        for line in ev.get("script", {}).get("exec", []):
                            fields |= extract_asserted_fields(line)

                if name and fields:
                    result[name] = fields

            for v in o.values():
                walk(v)

        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(collection)
    return result
def normalize_req_id(reqid):
    name, path = reqid.split("|", 1)
    return normalize_name(name), path

def tokenize(s):
    return set(re.findall(r'[a-z0-9]+', (s or "").lower()))
def normalize_name(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())

# -------------------------- New process_collection_with_common (endpoint-aware comment) --------------------------
def process_collection_with_common(collection, groups, global_fields, asserted_raw):
    """
    Comment request-level assertions ONLY when:
    - Field is promoted to collection-level
    - AND request URL matches the SAME group path
    """

    # ---------------- PROMOTED FIELDS BY GROUP PATH ----------------
    # Example:
    # ('api','reports','location') -> 'reports/location'
    PROMOTED_BY_PATH = {}

    for group_key, fields in groups.items():
        clean_tokens = [t for t in group_key if t not in ("api", "v1", "v2")]
        if not clean_tokens or not fields:
            continue
        path_key = "/".join(clean_tokens)
        PROMOTED_BY_PATH[path_key] = set(fields)

    ALL_PROMOTED_FIELDS = set().union(*PROMOTED_BY_PATH.values()) | global_fields

    # ---------------- HELPERS ----------------
    def extract_request_path(o):
        req = o.get("request")
        if not req:
            return ""
        url = req.get("url")
        raw = url.get("raw", "") if isinstance(url, dict) else str(url or "")
        return raw.split("?")[0]

    def is_cv_request(o):
        name = normalize_name(o.get("name", ""))
        return name.startswith("cv")

    def extract_asserted_fields_from_block(lines):
        fields = set()
        for ln in lines:
            fields |= extract_asserted_fields(ln)
        return fields

    def find_matching_group_fields(request_path):
        """
        Return promoted field-set ONLY if request path matches group path
        """
        for group_path, fields in PROMOTED_BY_PATH.items():
            if f"/{group_path}" in request_path:
                return fields
        return set()

    # ---------------- WALK & COMMENT ----------------
    def walk(o, current_path="", is_cv=False):
        if isinstance(o, dict):

            if "request" in o:
                current_path = extract_request_path(o)
                is_cv = is_cv_request(o)

            if "event" in o:
                for ev in o.get("event", []):
                    if ev.get("listen") != "test":
                        continue

                    script_obj = ev.get("script", {})

                    # 🔒 Skip protected scripts
                    if script_obj.get("_skip_common_processing"):
                        continue

                    old_exec = script_obj.get("exec", [])
                    new_exec = []

                    inside_test = False
                    brace_balance = 0
                    block_lines = []

                    # 🔑 PATH-BOUND promoted fields
                    path_promoted_fields = find_matching_group_fields(current_path)

                    for line in old_exec:
                        stripped = line.strip()
                        clean = stripped.lstrip("//").strip()

                        # ---------- START pm.test ----------
                        if not inside_test and clean.startswith("pm.test("):
                            inside_test = True
                            brace_balance = clean.count("{") - clean.count("}")
                            block_lines = [line]
                            continue

                        # ---------- INSIDE pm.test ----------
                        if inside_test:
                            block_lines.append(line)
                            brace_balance += clean.count("{") - clean.count("}")

                            if brace_balance == 0:
                                block_fields = extract_asserted_fields_from_block(block_lines)
                                comment_block = False

                                # 1️⃣ CV requests → always comment
                                if is_cv:
                                    comment_block = True

                                # 2️⃣ Status-only blocks → always comment
                                elif is_status_only_block(block_lines):
                                    comment_block = True

                                # 3️⃣ PATH + FIELD match → comment
                                elif (
                                    block_fields
                                    and block_fields.issubset(path_promoted_fields)
                                ):
                                    comment_block = True

                                for bl in block_lines:
                                    new_exec.append("// " + bl if comment_block else bl)

                                inside_test = False
                                block_lines = []
                            continue

                        # ---------- NON pm.test lines ----------
                        line_fields = extract_asserted_fields(line)

                        if (
                            line_fields
                            and line_fields.issubset(path_promoted_fields)
                        ):
                            new_exec.append("// " + line)
                        else:
                            new_exec.append(line)

                    ev["script"]["exec"] = new_exec

            for v in o.values():
                walk(v, current_path, is_cv)

        elif isinstance(o, list):
            for i in o:
                walk(i, current_path, is_cv)

    walk(collection)


# -------------------------- Existing LLM calls (kept as-is) --------------------------
# NOTE: These functions depend on your AZURE_URL and remote model. Kept unchanged from your prior code.

def generate_script_v22(old_script, type):
    prompt = f'''
<|system|>
You are a helpful assistant who is better than Postman's Postbot AI which fixes and converts old Postman scripts from legacy format (v2.1.0) to the modern format (v2.2.0). Retain the version as v2.1.0 in the schema only. If the script is empty, leave it empty.

<|user|>
Convert the following Postman {type} script to modern syntax. Schema version should stay v2.1.0. If the following aren't followed properly, I will end up losing my job so please follow these.
Instructions:
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
The following JSON structure is ONLY AN EXAMPLE to understand
nested access patterns and array handling.

IMPORTANT RULES:
- DO NOT assume these fields exist in the real response.
- DO NOT introduce new field names that are not already present
  in the original input script.
- Use this example ONLY to understand how to safely access
  objects vs arrays.
- If a field does not exist in the original script, DO NOT add it.

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
17. To validate a schema, do not use tv4.validate. Use Ajv instead. Example usage:
    const Ajv = require('ajv');
    const ajv = new Ajv();
    const schema = //given schema
    const validate = ajv.compile(schema);
    const isValid = validate(nr); //replace nr with the variable they are validating in the old script
    pm.test(pm.request.method + " " + request_path + " : Template is Valid", function () {
            'pm.expect(isValid).to.be.true;'
        });

18. Add a part with request method and request path and a colon before every test message.
    - Add the following script exactly and only to the collection level/folder level Pre-request script of the postman collection. DO NOT write this script in any Post-response test script.
        let unresolvedUrl = pm.request.url.toString().replace('{{domain}}', '').split('?')[0];
        pm.collectionVariables.set("request_url", unresolvedUrl); 
    - Add this line in the Post-response script of every request:
        let request_path = pm.collectionVariables.get("request_url");
    - Now add them in every test message like this: For example,
     If the test is : pm.test("Verify response structure", function...)
     Change the test name as : pm.test(pm.request.method + " " + request_path + " : " + "Verify response structure", function...)
    - This must be done for every test message in every script.
19. Merge all common basic tests: If separate pm.test or tests[..] blocks exist for: Status code is 200, code equals 0, message equals "success", merge them into one single pm.test.   
    - Test message format (exact pattern, the first part is default for all test messages):
      pm.request.method + " " + request_path + " : " + "Status code is " + pm.response.code + " code " + response.code + " message " + response.message
    - Example test name output:
      GET /api/example : Status code is 200 code 0 message success
    - The test should validate:
      pm.expect(pm.response.code).to.equal(200); //If the test in a particular script expects the status code to be 400 or any other code, put the same in this test too.
      pm.expect(jsonData.code).to.eql(0);
      pm.expect(jsonData.message).to.eql("success"); 
    - If the test for any one of them is not present in the script, validate for the other two that are present. For example: The script has separate tests only for status code and message. Then the test will be like:
      'pm.test(pm.request.method + " " + request_path + " " + "Status code is " + pm.response.code + " message " + response.message, function() {
          'pm.expect(pm.response.code).to.equal(200);' 
          'pm.expect(response.message).to.eql("success");'
       });'
    - If the tests for status code, code and message does not exist in the script, write a new merged test for status code, code and message with the pattern given.
    - This must be done for every request's test script.
    - IMPORTANT: Please add this test after the variables response and request_path are defined.
20. If the request name (pm.info.requestName) starts with 'CV_' or If its test script contains tests for "error code" and "message", you should add the following test as the first test after the variables response and request_path are defined.
    No need to add the test that is given in instruction 19 to this type of requests. Add only the following test:
    pm.test(pm.request.method + " " + request_path + " : Status code is " + pm.response.code + " message " + response.message, function() {
    'pm.expect(pm.response.code).to.equal(401);'
    });
21. If the script was already converted to new format postman script, DO NOT modify or add any extra script. Only follow instructions 18. and 19. for such scripts.
22. Common validations (fields that are repeated across multiple requests) will be promoted to collection-level test scripts by a later step.
    If a validation appears to be common or reusable, COMMENT it instead of rewriting or duplicating it. Do not preserve original common blocks if they are expected to be promoted.

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

{old_script}
'''
    messages = chat_history[-2:] + [{"role": "user", "content": prompt}]

    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": messages,
        "model": "gpt-4.1-mini"
    }
    chat_history[:] = chat_history[-6:]
    with AZURE_LOCK:
        try:
            response = AZURE_SESSION.post(api_url, json=payload, timeout=1600)
            response.raise_for_status()
            result=response.text.strip().strip('`\n"\' ')
        except requests.HTTPError as e:
        # Azure internal failure — cool down ONCE
            resp = e.response
            if resp is not None and resp.status_code >= 500:
                time.sleep(1.2)  # 🔑 critical
            raise  
    return result

def generate_postman_v22_again(oldpm_raw):
    prompt = f"""
<|system|>
You are a helpful assistant that corrects the format of the Postman v2.2.0 collection.

<|user|>
Update this collection to Postman v2.2.0 with proper test scripts (pm.test, pm.expect, pm.response). Retain v2.1.0 in the schema string.

```json
{oldpm_raw}
"""
    messages = chat_history[-2:] + [{"role": "user", "content": prompt}]
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": messages,
        "model": "gpt-4.1-mini"
    }
    chat_history[:] = chat_history[-6:]
    with AZURE_LOCK:
        try:
            response = AZURE_SESSION.post(api_url, json=payload, timeout=180)
            response.raise_for_status()
            fixed = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        except requests.HTTPError as e:
        # Azure internal failure — cool down ONCE
            resp = e.response
            if resp is not None and resp.status_code >= 500:
                time.sleep(1.2)
            raise
        
    return fixed

def fix_syntax_v22(truncated_script,old_script):
    prompt = f'''
<|system|>
You are an expert Postman script fixer that completes truncated or incomplete Postman scripts by correcting syntax issues. Your job is to fix the syntax errors in the provided script without changing its logic, structure, or variable names.
<|user|>
Continue from where the previous LLM's response truncated and return only the missing part not including the word from where it ended and **NOT THE ENTIRE SCRIPT**. If its already valid, return it as it is.
**DO NOT** change the logic, structure, or variable names. Only fix the syntax errors and return the corrected script as plain JavaScript only, without any comments or explanations.
This is the original script that was supposed to be converted by the previous LLM to v2.2.0:
---
{old_script}
---
This is the truncated or incomplete Postman script that you need to continue from:
---
{truncated_script}
---
'''
    messages = chat_history[-2:] + [{"role": "user", "content": prompt}]
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "message": messages,
        "model": "gpt-4.1-mini"
    }
    chat_history[:] = chat_history[-6:]
    with AZURE_LOCK:
        try:
            response = AZURE_SESSION.post(api_url, json=payload, timeout=3200)
            response.raise_for_status()
            result=response.text.strip().strip('`\n"\' ')
        except requests.HTTPError as e:
        # Azure internal failure — cool down ONCE
            resp = e.response
            if resp is not None and resp.status_code >= 500:
                time.sleep(1.2)
            raise
    return result
    

def generate_script_v22_fix(truncated_script, original_script, script_type):
    prompt = f'''
<|system|>
You are a helpful and an excellent assistant that completes truncated or incomplete Postman {script_type} scripts by continuing right off from where the previous LLM's response ended. The previous LLM response was truncated or incomplete. Your job is to finish the script correctly, preserving all logic, function names, and structure from the original input.

Instructions:
1. Do **not** rewrite the entire script.
2. Return **only** the missing or final portion needed to complete the truncated script.
3. Maintain all original function names and variable names.
4. Never change the logic, restructure blocks, or reword test descriptions.
5. Do not return duplicate or rewritten code. Only return what's missing from the end.
6. Return JavaScript only with no extra comments, no explanations, and no markdown.
7. If it is a global function in the pre request script, the format **should always be**:
`pm.globals.set(function_name, function_call() {{ ... }} + 'function_call()');` 
where function_name and function_call are placeholders for the actual global function name and function call.
8. Understand the JSON structure from the older script and see how the properties are called and follow that same manner but with the new code.

### Response structure:
The following JSON structure is ONLY AN EXAMPLE to understand
nested access patterns and array handling.

IMPORTANT RULES:
- DO NOT assume these fields exist in the real response.
- DO NOT introduce new field names that are not already present
  in the original input script.
- Use this example ONLY to understand how to safely access
  objects vs arrays.
- If a field does not exist in the original script, DO NOT add it.

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
4. Use the structure above to correctly navigate nested properties. For example:
    - `data.summary_details.down_count` should be accessed via `response.summary_details.down_count`
    - Never use `response.down_count` directly unless it is top-level (which it isn't in this example structure).
    - Always check if the parent (e.g., `summary_details`) exists before accessing its children.

<|user|>
The following is a truncated or incomplete Postman {script_type} script (output from a previous LLM call):
---
{truncated_script}
---

Here is the original input script that was supposed to be converted to the new format:
---
{original_script}
---

Please complete and repair the truncated output by appending from the end of the above truncated script the correct converted logic of the original script, returning the full, valid, and modernized Postman {script_type} script as plain JavaScript only. Do not add any extra comments, explanations, or markdown. Finally append your output to the input and check if it is a valid function before returning only your output.

'''
    messages = chat_history[-2:] + [{"role": "user", "content": prompt}]
    payload = {
        "systemprompt": "",
        "userprompt": prompt,
        "max_completion_tokens": 16000,
        "temperature": 0.15,
        "message": messages,
        "model": "gpt-4.1-mini"
    }
    chat_history[:] = chat_history[-6:]
    with AZURE_LOCK:
        try:
            response = AZURE_SESSION.post(api_url, json=payload, timeout=3200)
            response.raise_for_status()
            result=response.text.strip().strip('`\n"\' ')
        except requests.HTTPError as e:
        # Azure internal failure — cool down ONCE
            resp = e.response
            if resp is not None and resp.status_code >= 500:
                time.sleep(1.2)
            raise
       
    return result

# -------------------------- Conversion walk + integration (unchanged) --------------------------

def is_global_utility_script(script_text: str) -> bool:
    """
    Detect scripts that DEFINE global utilities.
    These must never be converted or modified.
    """
    text = script_text.lower()

    # Global setters that define reusable logic
    if "pm.globals.set(" in text:
        # If it defines a function, arrow fn, or eval payload
        if "function" in text or "=>" in text or "eval(" in text:
            return True

    return False


def convert_scripts_in_collection(obj, parent_listen=None, is_collection_root=False):
    if isinstance(obj, dict):

        # 🔒 Detect collection root ONCE
        if not is_collection_root and "info" in obj and "item" in obj:
            is_collection_root = True

        # ---------------- WALK EVENTS FIRST ----------------
        if "event" in obj and isinstance(obj["event"], list):
            for event in obj["event"]:
                listen_type = event.get("listen", None)
                if "script" in event:
                    convert_scripts_in_collection(
                        event,
                        parent_listen=listen_type,
                        is_collection_root=is_collection_root
                    )

        # ---------------- SCRIPT HANDLING ----------------
        if (
            "script" in obj
            and isinstance(obj["script"], dict)
            and "exec" in obj["script"]
        ):
            value = obj["script"]
            if value.get("_converted_once"):
                return
            value["_converted_once"] = True
            old_exec = value.get("exec", [])

            script_text = (
                "\n".join(old_exec)
                if isinstance(old_exec, list)
                else str(old_exec)
            )
            script_type = (
            parent_listen if parent_listen in ("prerequest", "test") else "test"
   )

            if len(script_text) > 120_000:
                chunks = split_large_script(script_text)
                converted = []

                for ch in chunks:
                    part = generate_script_v22(ch, script_type)
                    converted.append(part)

                full_script = "\n".join(converted)
                value["exec"] = full_script.splitlines()
                return


            # 🔒 COLLECTION-LEVEL → DO NOT MODIFY, BUT DO NOT RETURN
            if is_collection_root and parent_listen in ("prerequest", "test"):
                value["_skip_conversion"] = True

            # 🔒 EMPTY SCRIPT → PRESERVE
            if isinstance(old_exec, list) and all(line.strip() == "" for line in old_exec):
                value["exec"] = []
                value["_skip_common_processing"] = True

            # 🔒 GLOBAL UTILITY → PRESERVE CONTENT, BUT CONTINUE WALK
            elif is_global_utility_script(script_text):
                value["_skip_common_processing"] = True

            # ---------------- CONVERSION ----------------
            else:
                try:
                    script_type = (
                        parent_listen if parent_listen in ("prerequest", "test") else "test"
                    )

                    new_script = generate_script_v22(script_text, script_type)
                    cleaned_script = new_script.strip()

                    for prefix in ("javascript", "js"):
                        if cleaned_script.lower().startswith(prefix):
                            cleaned_script = cleaned_script[len(prefix):].lstrip(":").lstrip("\n").lstrip()

                    def is_truncated(s):
                        stack = []
                        pairs = {")": "(", "}": "{", "]": "["}
                        for c in s:
                            if c in "({[":
                                stack.append(c)
                            elif c in ")}]":
                                if not stack or stack[-1] != pairs[c]:
                                    return True
                                stack.pop()
                        return bool(stack)

                    

                    # If LLM returned nothing → preserve original
                    if not cleaned_script:
                        value["exec"] = old_exec
                        value["_skip_common_processing"] = True

                    else:
                        # 🔧 FIX TRUNCATION
                        if is_truncated(cleaned_script):
                            max_attempts = 3
                            attempts = 0

                            while is_truncated(cleaned_script) and attempts < max_attempts:
                                fixed_script = generate_script_v22_fix(
                                    cleaned_script, script_text, script_type
                                )
                                new_part = fixed_script.strip()

                                for prefix in ("javascript", "js"):
                                    if new_part.lower().startswith(prefix):
                                        new_part = new_part[len(prefix):].lstrip(":").lstrip("\n").lstrip()

                                cleaned_script += new_part

                                attempts += 1

                            if is_truncated(cleaned_script):
                                fixed_script = fix_syntax_v22(cleaned_script, script_text)
                                new_part = fixed_script.strip()

                                for prefix in ("javascript", "js"):
                                    if new_part.lower().startswith(prefix):
                                        new_part = new_part[len(prefix):].lstrip(":").lstrip("\n").lstrip()

                                cleaned_script += new_part
                                

                        value["exec"] = cleaned_script.splitlines()

                except Exception as e:
                    st.warning(f"Script conversion failed: {e}")

        # ---------------- WALK ITEMS ----------------
        if "item" in obj and isinstance(obj["item"], list):
            for subitem in obj["item"]:
                convert_scripts_in_collection(
                    subitem,
                    parent_listen=parent_listen,
                    is_collection_root=is_collection_root
                )

        # ---------------- WALK OTHER KEYS ----------------
        for key, value in obj.items():
            if key not in ("event", "script", "item"):
                convert_scripts_in_collection(
                    value,
                    parent_listen=parent_listen,
                    is_collection_root=is_collection_root
                )

    elif isinstance(obj, list):
        for item in obj:
            convert_scripts_in_collection(
                item,
                parent_listen=parent_listen,
                is_collection_root=is_collection_root
            )
# ===================== SESSION STATE FIX (ONLY ADDITION) =====================
st.set_page_config(page_title="Postman Bulk Converter")

if "conversion_done" not in st.session_state:
    st.session_state.conversion_done = False

if "zip_buffer" not in st.session_state:
    st.session_state.zip_buffer = None

if "zip_bytes" not in st.session_state:
    st.session_state.zip_bytes = None
# ============================================================================
# ===================== RUN CONVERSION ONLY ONCE =====================
if not st.session_state.conversion_done:

    # -------------------------------------------------
    # Environment setup
    # -------------------------------------------------
    load_dotenv()

    api_url = os.getenv("AZURE_URL")
    if not api_url or not api_url.strip():
        st.error(
            "AZURE_URL is not set in your .env file or is empty. "
            "Please check your .env configuration."
        )
        st.stop()

    # -------------------------------------------------
    # Load Postman v2.2 schema (local fallback)
    # -------------------------------------------------
    schema = None
    try:
        with open("postman_collection_v2.2_schema.json", "r", encoding="utf-8") as f:
            schema = json.load(f)
        st.warning("Loaded Postman v2.2 schema from local fallback.")
    except Exception as fallback_error:
        st.error(f"Failed to load Postman schema.\n\nError: {fallback_error}")
        st.stop()

    # -------------------------------------------------
    # Streamlit UI
    # -------------------------------------------------
    
    st.title("Convert All Postman JSONs from a Zipped Folder")

    uploaded_zip = st.file_uploader(
        "Upload a zipped folder of Postman collections (.zip)",
        type="zip",
        key="postman_zip"
    )

    uploaded_log = st.file_uploader(
        "(Required) Upload a Newman/Jenkins run log (.txt) to auto-detect common validations",
        type="txt",
        key="newman_log"
    )

    COMMON_THRESHOLD = 2

    # -------------------------------------------------
    # Enforce both uploads
    # -------------------------------------------------
    if not uploaded_zip or not uploaded_log:
        st.info(
            "Please upload BOTH:\n"
            "1) a zipped folder of Postman collections (.zip)\n"
            "2) the Newman/Jenkins run log (.txt)\n\n"
            "Conversion will start only after both files are provided."
        )
        st.stop()

    # -------------------------------------------------
    # Cache ZIP BYTES (CRITICAL FIX)
    # -------------------------------------------------
    if st.session_state.zip_bytes is None:
        st.session_state.zip_bytes = uploaded_zip.getvalue()
    if "log_bytes" not in st.session_state:
        st.session_state.log_bytes = uploaded_log.getvalue()

    # -------------------------------------------------
    # Save uploaded log to temp location
    # -------------------------------------------------
    try:
        tmp_log_dir = tempfile.mkdtemp()
        tmp_log_path = os.path.join(tmp_log_dir, "uploaded_newman_log.txt")

        with open(tmp_log_path, "wb") as f:
            f.write(st.session_state.log_bytes)

        log_path_to_use = tmp_log_path
    except Exception as e:
        st.error(f"Failed to save uploaded log: {e}")
        st.stop()

    # -------------------------------------------------
    # Parse Newman/Jenkins log
    # -------------------------------------------------
    with open(log_path_to_use, "r", encoding="utf-8") as f:
        print("RAW LOG SIZE:", len(f.read()))


    try:
        per_request_validations = parse_newman_log_from_path(log_path_to_use)
        print("PARSED REQUEST COUNT:", len(per_request_validations))
    except Exception as e:
        st.error(f"Failed to parse the log file: {e}")
        st.stop()

    # -------------------------------------------------
    # Process ZIP and convert collections
    # -------------------------------------------------
    if uploaded_zip:

        with tempfile.TemporaryDirectory() as tmpdir:
            # -------------------------------------------------
            # Write ZIP
            # -------------------------------------------------
            zip_path = os.path.join(tmpdir, "uploaded.zip")
            with open(zip_path, "wb") as f:
                f.write(st.session_state.zip_bytes)

            # -------------------------------------------------
            # Extract ZIP
            # -------------------------------------------------
            extract_dir = os.path.join(tmpdir, "unzipped")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_dir)

            st.success("Zip extracted. Starting script conversion...")

            # -------------------------------------------------
            # Converted dir (LIVES for entire block)
            # -------------------------------------------------
            converted_dir = os.path.join(tmpdir, "converted")
            os.makedirs(converted_dir, exist_ok=True)

            converted_files = 0

            total_files = sum(
                1
                for root, _, files in os.walk(extract_dir)
                for file in files
                if file.endswith(".json")
            )

            progress_bar = st.progress(0)
            progress_text = st.empty()

            start_time = time.time()
            processed_files = 0

            # -------------------------------------------------
            # MAIN LOOP (UNCHANGED FUNCTION CALLS)
            # -------------------------------------------------
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if not file.endswith(".json"):
                        continue

                    input_path = os.path.join(root, file)

                    try:
                        with open(input_path, "r", encoding="utf-8") as f:
                            raw = f.read()
                            collection_json = json.loads(raw)

                        if "item" not in collection_json:
                            st.warning(f"{file} skipped: No 'item' key found.")
                            processed_files += 1
                            continue

                        # === EXISTING CALLS (UNCHANGED) ===
                        collection_requests = extract_collection_requests(collection_json)
                        clusters = cluster_requests_by_path(collection_requests)
                        clusters = {
                            k: v for k, v in clusters.items()
                            if not (len(k) == 1 and k[0] == "api")
                        }
                        print("CLUSTERS FOUND:", list(clusters.keys()))

                        asserted_raw = collect_asserted_fields_per_request_raw(collection_json)

                        groups, global_fields = map_common_validations_to_groups(
                            per_request_validations,
                            asserted_raw,
                            clusters,
                            threshold=COMMON_THRESHOLD
                        )

                        print("GROUP KEYS:", list(groups.keys()))
                        print("GLOBAL FIELDS SAMPLE:", list(global_fields)[:10])
                        print("GROUPS BOOL:", bool(groups),
                        "DETAIL:", {k: len(v) for k, v in groups.items()})
                        total_common = sum(len(v) for v in groups.values())
                        sample_display = {
                        "/".join(k): sorted(list(v))[:10]
                        for k, v in groups.items()
                    }

                        st.success(
                        f"Detected {len(per_request_validations)} requests in log. "
                        f"Common variables/groups detected (threshold={COMMON_THRESHOLD}): "
                        f"{total_common}"
                    )
                        st.write("Groups sample:", sample_display)
                        convert_scripts_in_collection(collection_json)

                        prereq_lines, test_lines = generate_collection_level_scripts_from_common(groups)

                        coll_events = collection_json.get("event", [])
                        coll_events = [ev for ev in coll_events if ev.get("listen") != "prerequest"]
                        prereq_exists = any(
                        ev.get("listen") == "prerequest"
                        for ev in coll_events
                    )
                        if prereq_lines and not prereq_exists:
                            coll_events.insert(
                                0,
                                {
                                    "listen": "prerequest",
                                    "script": {"type": "text/javascript", "exec": prereq_lines},
                                }
                            )

                        if groups and test_lines:
                            process_collection_with_common(
                                collection_json, groups, global_fields, asserted_raw
                            )
                            if any(line.strip() for line in test_lines):
                                coll_events.append(
                                {
                                    "listen": "test",
                                    "script": {"type": "text/javascript", "exec": test_lines},
                                }
                            )

                        
                        if "event" not in collection_json or not isinstance(collection_json["event"], list):
                            collection_json["event"] = []

                        def is_empty_event(ev):
                            exec_lines = ev.get("script", {}).get("exec", [])
                            return (
                                ev.get("listen") == "test"
                                and isinstance(exec_lines, list)
                                and all(not line.strip() for line in exec_lines)
                            )

                        coll_events = [
                            ev for ev in coll_events
                            if not is_empty_event(ev)
                        ]

                        collection_json["event"] = coll_events
                        print("COLLECTION EVENTS:",
                            [e["listen"] for e in collection_json["event"]])

                        # === VALIDATE + WRITE (CRITICAL FIX) ===
                        try:
                            validate_as_v22_but_save_as_v21(
                                json.loads(json.dumps(collection_json)),
                                schema
                            )
                        except Exception as ve:
                            st.warning(f"{file}: validation failed, but file was exported")

                        out_path = os.path.join(
                            converted_dir,
                            f"{Path(file).stem}_converted.json"
                        )

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
                    eta = datetime.now() + timedelta(seconds=est_time_left)

                    progress_bar.progress(processed_files / total_files if total_files else 1)
                    progress_text.text(
                        f"Processed {processed_files}/{total_files} files. "
                        f"Time left: {mins}m {secs}s. ETA: {eta.strftime('%H:%M:%S')}"
                    )

                    time.sleep(0.01)

            # -------------------------------------------------
            # FINAL ZIP (INSIDE SAME TEMP DIR)
            # -------------------------------------------------
            if converted_files == 0:
                st.warning("No valid .json files were converted.")
            else:
                zip_buffer = BytesIO()
                with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
                    for file in os.listdir(converted_dir):
                        zipf.write(
                            os.path.join(converted_dir, file),
                            arcname=file
                        )

                zip_buffer.seek(0)

                # ✅ Store ONLY bytes, not paths
                st.session_state.zip_bytes = zip_buffer.getvalue()

                st.session_state.conversion_done = True
                st.session_state.converted_files = converted_files


if st.session_state.conversion_done:
    st.markdown("---")
    st.success(f"Converted {st.session_state.converted_files} collections successfully.")

if st.session_state.conversion_done:
    st.download_button(
        label="Download Converted Collections (.zip)",
        data=st.session_state.zip_bytes,
        file_name="new_converted_jsons.zip",
        mime="application/zip"
    )
