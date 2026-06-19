from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from io import BytesIO

import re
import json
import os
import time
import tempfile
import zipfile
import requests
import hashlib
import shutil
import struct
import uuid as uuid_module
import asyncio
import concurrent.futures
import binascii
import base64
import hashlib as _hashlib
import datetime
import platform

import colorama
from colorama import Fore, Style
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, AES

colorama.init()

TITLE_ID = "20CA2"
TITLE_SHARED_SECRET = "S8RS53ZEIGMYTYG856U3U19AORWXQXF41J7FT3X9YCWAC7I35X"

PLAYFAB_HEADERS = {
    "User-Agent": "libhttpclient/1.0.0.0",
    "Content-Type": "application/json",
    "Accept-Language": "en-US"
}

PLAYFAB_SESSION = requests.Session()
PLAYFAB_SESSION.headers.update(PLAYFAB_HEADERS)
PLAYFAB_DOMAIN = "https://" + TITLE_ID.lower() + ".playfabapi.com"

SETTING_FILE = "settings.json"
PLAYFAB_SETTINGS = {}


def _pf_send(endpoint, data, hdrs={}):
    response = PLAYFAB_SESSION.post(PLAYFAB_DOMAIN + endpoint, json=data, headers=hdrs).json()
    if response['code'] != 200:
        return response
    return response['data']


def _pf_gen_custom_id():
    return "MCPF" + binascii.hexlify(os.urandom(16)).decode("UTF-8").upper()


def _pf_gen_player_secret():
    return base64.b64encode(os.urandom(32)).decode("UTF-8")


def _pf_get_mojang_csp():
    return base64.b64decode(_pf_send("/Client/GetTitlePublicKey", {
        "TitleId": TITLE_ID,
        "TitleSharedSecret": TITLE_SHARED_SECRET
    })['RSAPublicKey'])


def _pf_import_csp_key(csp):
    e = struct.unpack("I", csp[0x10:0x14])[0]
    n = bytearray(csp[0x14:])
    n.reverse()
    n = int(binascii.hexlify(n), 16)
    return RSA.construct((n, e))


def _pf_gen_timestamp():
    return datetime.datetime.now().isoformat() + "Z"


def _pf_gen_signature(request_body, timestamp):
    sha256 = _hashlib.sha256()
    sha256.update(
        request_body.encode("UTF-8") + b"." +
        timestamp.encode("UTF-8") + b"." +
        pf_config_get("PLAYER_SECRET").encode("UTF-8")
    )
    return base64.b64encode(sha256.digest())


def pf_config_load():
    global PLAYFAB_SETTINGS, SETTING_FILE
    if os.path.exists(SETTING_FILE):
        PLAYFAB_SETTINGS = json.loads(open(SETTING_FILE, "r").read())


def pf_config_get(key):
    global PLAYFAB_SETTINGS
    pf_config_load()
    return PLAYFAB_SETTINGS.get(key)


def pf_config_set(key, new_value):
    global PLAYFAB_SETTINGS
    pf_config_load()
    PLAYFAB_SETTINGS[key] = new_value
    open(SETTING_FILE, "w").write(json.dumps(PLAYFAB_SETTINGS))
    return new_value


def pf_login_with_custom_id():
    custom_id = pf_config_get("CUSTOM_ID")
    player_secret = pf_config_get("PLAYER_SECRET")
    create_new_account = False

    if custom_id is None:
        custom_id = _pf_gen_custom_id()
        create_new_account = True
    if player_secret is None:
        player_secret = _pf_gen_player_secret()
        create_new_account = True

    pf_config_set("CUSTOM_ID", custom_id)
    pf_config_set("PLAYER_SECRET", player_secret)

    base_payload = {
        "InfoRequestParameters": {
            "GetCharacterInventories": False, "GetCharacterList": False,
            "GetPlayerProfile": True, "GetPlayerStatistics": False,
            "GetTitleData": False, "GetUserAccountInfo": True,
            "GetUserData": False, "GetUserInventory": False,
            "GetUserReadOnlyData": False, "GetUserVirtualCurrency": False,
            "PlayerStatisticNames": None, "ProfileConstraints": None,
            "TitleDataKeys": None, "UserDataKeys": None, "UserReadOnlyDataKeys": None
        },
        "TitleId": TITLE_ID
    }

    req = None
    attempt = 0
    max_attempts = 5

    while attempt < max_attempts:
        if create_new_account:
            new_payload = {
                "CreateAccount": True,
                "TitleId": TITLE_ID,
                "InfoRequestParameters": base_payload["InfoRequestParameters"]
            }
            to_enc = json.dumps({"CustomId": custom_id, "PlayerSecret": player_secret}).encode("UTF-8")
            pub_key = _pf_import_csp_key(_pf_get_mojang_csp())
            cipher = PKCS1_OAEP.new(pub_key)
            ciphertext = cipher.encrypt(to_enc)
            new_payload["EncryptedRequest"] = base64.b64encode(ciphertext).decode("UTF-8")
            req = _pf_send("/Client/LoginWithCustomID", new_payload)
            pf_config_set("CUSTOM_ID", custom_id)
            pf_config_set("PLAYER_SECRET", player_secret)
        else:
            login_payload = {
                "CustomId": custom_id, "CreateAccount": False,
                "TitleId": TITLE_ID,
                "InfoRequestParameters": base_payload["InfoRequestParameters"]
            }
            ts = _pf_gen_timestamp()
            sig = _pf_gen_signature(json.dumps(login_payload), ts)
            req = _pf_send("/Client/LoginWithCustomID", login_payload,
                           {"X-PlayFab-Signature": sig, "X-PlayFab-Timestamp": ts})
            if req and "errorCode" in req and req["errorCode"] == 1001:
                custom_id = _pf_gen_custom_id()
                player_secret = _pf_gen_player_secret()
                create_new_account = True
                continue

        if req and "EntityToken" in req:
            break
        if req and "errorCode" in req and req["errorCode"] == 1199:
            wait_time = req.get("retryAfterSeconds", 5)
            time.sleep(wait_time)
            attempt += 1
            continue

        attempt += 1
        time.sleep(2)

    if not req or "EntityToken" not in req:
        raise Exception("PlayFab login failed after multiple attempts.")

    entity_token = req["EntityToken"]["EntityToken"]
    PLAYFAB_SESSION.headers.update({"X-EntityToken": entity_token})
    return req


def pf_get_entity_token(playfab_id, acc_type):
    req = _pf_send("/Authentication/GetEntityToken", {
        "Entity": {"Id": playfab_id, "Type": acc_type}
    })
    PLAYFAB_SESSION.headers.update({"X-EntityToken": req["EntityToken"]})
    return req


def pf_search_by_ids(query, order_by, select, top, skip, custom_ids):
    if isinstance(custom_ids, str):
        filter_query = f"Id eq '{custom_ids}'"
    elif isinstance(custom_ids, list):
        filter_query = " or ".join([f"Id eq '{i}'" for i in custom_ids])
    else:
        raise ValueError("custom_ids must be str or list")
    return _pf_send("/Catalog/Search", {
        "count": True, "query": query,
        "filter": filter_query, "orderBy": order_by,
        "scid": "4fc10100-5f7a-4470-899b-280835760c07",
        "select": select, "top": top, "skip": skip
    })


def pf_search_friendly_uuid(query, order_by, select, top, skip, custom_ids):
    if not isinstance(custom_ids, list):
        raise ValueError("custom_ids must be list")
    filter_query = " or ".join([
        f"contentType eq 'MarketplaceDurableCatalog_V1.2' and tags/any(t: t eq '{i}')"
        for i in custom_ids
    ])
    return _pf_send("/Catalog/Search", {
        "count": True, "query": query,
        "filter": filter_query, "orderBy": order_by,
        "scid": "4fc10100-5f7a-4470-899b-280835760c07",
        "select": select, "top": top, "skip": skip
    })


def pf_search_name(query, order_by, select, top, skip, search_type, search_term=None):
    base_filter = "(contentType eq 'MarketplaceDurableCatalog_V1.2')"
    tags_filter = {
        "texture": "tags/any(t: t eq 'resourcepack')",
        "mashup": "tags/any(t: t eq 'mashup')",
        "addon": "tags/any(t: t eq 'addon')",
        "persona": "(contentType eq 'PersonaDurable')",
        "capes": "(displayProperties/pieceType eq 'persona_capes')",
        "hidden": "tags/any(t: t eq 'hidden_offer')",
        "skin": "tags/any(t: t eq 'skinpack')"
    }

    if search_type in ["name", "hidden", "newest", "skin"]:
        filter_query = base_filter
        if search_type == "hidden":
            filter_query += f" and {tags_filter['hidden']}"
            search_query = None
        elif search_type == "skin":
            filter_query += f" and {tags_filter['skin']}"
            search_query = None
        elif search_type == "newest":
            filter_query = base_filter
            search_query = None
        else:
            search_query = f'"{search_term}"'

        payload = {
            "count": True, "query": query,
            "filter": filter_query, "orderBy": "creationDate DESC",
            "scid": "4fc10100-5f7a-4470-899b-280835760c07",
            "select": select, "top": top, "skip": skip,
            "search": search_query
        }
        response = _pf_send("/Catalog/Search", payload)
        return response.get("Items", [])

    else:
        if search_type == "texture":
            filter_query = f"{base_filter} and {tags_filter['texture']}"
            search_query = None
        elif search_type == "mashup":
            filter_query = f"{base_filter} and {tags_filter['mashup']}"
            search_query = None
        elif search_type == "addon":
            filter_query = f"{base_filter} and {tags_filter['addon']}"
            search_query = None
        elif search_type == "allhidden":
            filter_query = f"{base_filter} and {tags_filter['hidden']}"
            search_query = None
        elif search_type == "persona":
            filter_query = tags_filter["persona"]
            search_query = str(search_term)
        elif search_type == "capes":
            filter_query = tags_filter["capes"]
            search_query = None
        else:
            filter_query = base_filter
            search_query = None

        all_items = []
        while True:
            payload = {
                "count": True, "query": query,
                "filter": filter_query, "orderBy": order_by,
                "scid": "4fc10100-5f7a-4470-899b-280835760c07",
                "select": select, "top": top, "skip": skip
            }
            if search_query:
                payload["search"] = search_query
            response = _pf_send("/Catalog/Search", payload)
            total_count = response.get("Count", 0)
            items = response.get("Items", [])
            all_items.extend(items)
            if len(items) < top or len(all_items) >= total_count:
                break
            skip += top
            if total_count - skip < top:
                top = total_count - skip
        return all_items


def playfab_main(custom_id_list):
    MAX_SEARCH = 300
    results_dict = {}
    if isinstance(custom_id_list, str):
        custom_id_list = [custom_id_list]

    for i in range(0, len(custom_id_list), MAX_SEARCH):
        chunk = custom_id_list[i:i + MAX_SEARCH]
        search_result = pf_search_by_ids("", "creationDate DESC", "contents,images", MAX_SEARCH, 0, chunk)
        search_results = search_result.get("Items", [])
        if search_results:
            results_dict.update({item["Id"]: item for item in search_results})

    return results_dict


def dlc_aes256_cfb_decrypt(key, iv, data):
    decryptor = AES.new(key, AES.MODE_CFB, iv)
    return decryptor.decrypt(data)


def dlc_read_and_decrypt(file_path, skin_key=None, keys_file=None):
    with open(file_path, 'rb') as f:
        header = f.read(17)
        _, magic, _, uuid_length = struct.unpack('<IIQb', header)
        file_uuid = f.read(uuid_length).decode('utf-8')
        if magic != 0x9BCFB9FC:
            raise ValueError("Not a valid contents.json file.")
        key = skin_key if skin_key else dlc_get_key_from_tsv(keys_file, file_uuid)
        if not key:
            raise ValueError("Key not found for the DLC")
        f.seek(0x100)
        encrypted_data = f.read()
        iv = key[:16]
        return dlc_aes256_cfb_decrypt(key, iv, encrypted_data), file_uuid


def dlc_get_key_from_tsv(keys_file, file_uuid):
    if isinstance(keys_file, str):
        keys_file = [keys_file]
    for fname in keys_file:
        if os.path.exists(fname):
            with open(fname, 'r') as f:
                for line in f:
                    fields = line.strip().split('\t')
                    if len(fields) >= 4 and fields[1] == file_uuid:
                        return fields[3].encode('utf-8')
    return None


def dlc_decrypt_custom_file(file_path, key):
    with open(file_path, 'rb') as f:
        encrypted_data = f.read()
    iv = key[:16]
    return dlc_aes256_cfb_decrypt(key, iv, encrypted_data)


def dlc_find_files(directory, file_name=None):
    for root, _, filenames in os.walk(directory):
        for filename in filenames:
            if file_name is None or filename == file_name:
                yield os.path.join(root, filename)


def dlc_decrypt_and_write_file(target_file_path, key, first_uuid):
    try:
        decrypted = dlc_decrypt_custom_file(target_file_path, key.encode('utf-8'))
        with open(target_file_path, 'wb') as f:
            f.write(decrypted)
    except Exception as e:
        _log_error(first_uuid, e)


def dlc_decrypt_files_for_contents_json(contents_json_file, key, first_uuid):
    try:
        with open(contents_json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except UnicodeDecodeError:
        raise
    content_array = data.get("content", [])
    total_files = len(content_array)
    files_processed = 0
    with ThreadPoolExecutor() as executor:
        futures = []
        for entry in content_array:
            if "key" in entry and "path" in entry:
                target = os.path.join(os.path.dirname(contents_json_file), entry["path"])
                futures.append(executor.submit(dlc_decrypt_and_write_file, target, entry["key"], first_uuid))
        for fut in futures:
            fut.result()
            files_processed += 1


def dlc_decrypt_files(root_directory, keys_file, first_uuid):
    for cf in list(dlc_find_files(root_directory, "contents.json")):
        dec, _ = dlc_read_and_decrypt(cf, keys_file=keys_file)
        with open(cf, 'wb') as f:
            f.write(dec)
        dlc_decrypt_files_for_contents_json(cf, keys_file, first_uuid)

    db_folder = os.path.join(root_directory, "db")
    db_files = list(dlc_find_files(db_folder))
    for db_file in db_files:
        if os.path.getsize(db_file) == 0 or "lost" in os.path.dirname(db_file):
            continue
        dec, _ = dlc_read_and_decrypt(db_file, keys_file=keys_file)
        with open(db_file, 'wb') as f:
            f.write(dec)


def dlc_modify_file(file_path):
    with open(file_path, "r+b") as f:
        data = f.read()
        data = data.replace(b"prid", b"pria")
        f.seek(0)
        f.write(data)
        f.truncate()


def dlc_modify_level_dat(root_directory):
    level_dat = os.path.join(root_directory, "level.dat")
    if os.path.exists(level_dat):
        dlc_modify_file(level_dat)
        return True
    return False


def dlc_detect_encoding(file_path):
    with open(file_path, 'rb') as f:
        raw = f.read()
    try:
        raw.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        return 'latin1'


def dlc_remove_forbidden_chars(name):
    for ch in [':', '?', '/', '<', '>', '\\', '|', '*']:
        name = name.replace(ch, '')
    return name


def dlc_get_pack_name(extracted_folder_path):
    lang_path = os.path.join(extracted_folder_path, "texts", "en_US.lang")
    enc = dlc_detect_encoding(lang_path)
    with open(lang_path, 'r+', encoding=enc) as f:
        lines = f.readlines()
        f.seek(0)
        for line in lines:
            if line.startswith("pack.name="):
                line = line.replace('&', '')
            f.write(line)
        f.truncate()

    with open(lang_path, 'rb') as f:
        bom = f.read(3)
        if bom != b'\xef\xbb\xbf':
            f.seek(0)
        for line in f:
            try:
                decoded = line.decode('utf-8')
            except UnicodeDecodeError:
                decoded = line.decode('utf-8', errors='ignore')
            parts = decoded.split('#', 1)
            decoded = parts[0].strip() if len(parts) > 1 else decoded
            if decoded.startswith("pack.name="):
                name = decoded[len("pack.name="):].strip().replace('\t', ' ')
                return dlc_remove_forbidden_chars(name)
    return None


def dlc_get_folder_type(folder_path, pack_name):
    with open(os.path.join(folder_path, "manifest.json"), 'r') as f:
        manifest = json.load(f)
    for module in manifest.get("modules", []):
        t = module.get("type", "")
        if t == "resources":
            return f"{pack_name} RP"
        elif t == "data":
            return f"{pack_name} BP"
    return "Unknown"


def dlc_compress_files_zip(source_folders, pack_name, output_folder, is_addon=False):
    if is_addon:
        base = os.path.join(output_folder, f"{pack_name} (addon)")
        ext = ".mcaddon"
    else:
        src = source_folders if isinstance(source_folders, str) else source_folders[0]
        if os.path.exists(os.path.join(src, "level.dat")):
            base = os.path.join(output_folder, f"{pack_name} (world_template)")
            ext = ".mctemplate"
        else:
            base = os.path.join(output_folder, f"{pack_name} (resources)")
            ext = ".mcpack"

    zip_path = f"{base}{ext}"
    i = 1
    while os.path.exists(zip_path):
        i += 1
        zip_path = f"{base}_{i}{ext}"

    def process_file(args):
        filepath, arcname = args
        try:
            buf = BytesIO()
            with open(filepath, 'rb') as f:
                buf.write(f.read())
            return arcname, buf.getvalue()
        except Exception:
            return None

    files_to_process = []
    if is_addon:
        for folder in source_folders:
            folder_type = dlc_get_folder_type(folder, pack_name)
            for root, _, files in os.walk(folder):
                for file in files:
                    if file not in ["signatures.json", "contents.json"]:
                        fp = os.path.join(root, file)
                        an = os.path.join(folder_type, os.path.relpath(fp, folder))
                        files_to_process.append((fp, an))
    else:
        src = source_folders if isinstance(source_folders, str) else source_folders[0]
        for root, _, files in os.walk(src):
            for file in files:
                if file not in ["signatures.json", "contents.json"]:
                    fp = os.path.join(root, file)
                    files_to_process.append((fp, os.path.relpath(fp, src)))

    with ThreadPoolExecutor() as executor:
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for result in executor.map(process_file, files_to_process):
                if result:
                    arcname, data = result
                    zipf.writestr(arcname, data)

    return zip_path


def dlc_get_skin_pack_name(extracted_folder_path):
    lang_path = os.path.join(extracted_folder_path, "texts", "en_US.lang")
    enc = dlc_detect_encoding(lang_path)
    with open(lang_path, 'r+', encoding=enc) as f:
        content = f.read()
        f.seek(0)
        f.write(content.replace('&', ''))
        f.truncate()

    with open(lang_path, 'rb') as f:
        bom = f.read(3)
        if bom != b'\xef\xbb\xbf':
            f.seek(0)
        first_line = None
        persona_present = False
        for line in f:
            if b'skinpack' in line or b'persona' in line:
                first_line = line
                persona_present = b'persona' in line
                break

    try:
        decoded = first_line.decode('utf-8')
    except UnicodeDecodeError:
        decoded = first_line.decode('utf-8', errors='ignore')

    name = decoded.split('=')[-1].strip().replace('\t', ' ')
    if persona_present and not name:
        parts = decoded.split('.')
        name = parts[1] if len(parts) > 1 else ""
    return dlc_remove_forbidden_chars(name), persona_present


def dlc_modify_skin_json(root_directory):
    for dirpath, _, filenames in os.walk(root_directory):
        for filename in filenames:
            if filename == "skins.json":
                sk_path = os.path.join(dirpath, filename)
                with open(sk_path, "r+") as f:
                    data = f.read().replace("paid", "free")
                    f.seek(0)
                    f.write(data)
                    f.truncate()


def dlc_replace_uuids_in_manifest(manifest_path):
    with open(manifest_path, "r+") as f:
        data = json.load(f)
        data["header"]["uuid"] = str(uuid_module.uuid4())
        for module in data.get("modules", []):
            module["uuid"] = str(uuid_module.uuid4())
        f.seek(0)
        json.dump(data, f, separators=(',', ':'))
        f.truncate()


def dlc_decrypt_files_for_contents_json_skin(contents_json_file):
    with open(contents_json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for entry in data.get("content", []):
        if "key" in entry and "path" in entry:
            target = os.path.join(os.path.dirname(contents_json_file), entry["path"])
            dec = dlc_decrypt_custom_file(target, entry["key"].encode('utf-8'))
            with open(target, 'wb') as f2:
                f2.write(dec)


def dlc_decrypt_files_skins(root_directory, skin_key):
    for cf in dlc_find_files(root_directory, "contents.json"):
        dec, _ = dlc_read_and_decrypt(cf, skin_key=skin_key)
        with open(cf, 'wb') as f:
            f.write(dec)
        dlc_decrypt_files_for_contents_json_skin(cf)


def dlc_skin_main(pack_folder, output_folder=None):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    if not os.path.isdir(pack_folder):
        return
    if list(dlc_find_files(pack_folder, "contents.json")):
        skin_key = b's5s5ejuDru4uchuF2drUFuthaspAbepE'
        dlc_decrypt_files_skins(pack_folder, skin_key)
        dlc_modify_skin_json(pack_folder)
        dlc_replace_uuids_in_manifest(os.path.join(pack_folder, "manifest.json"))
        skin_pack_name, persona_present = dlc_get_skin_pack_name(pack_folder)

        ext = "(persona).zip" if persona_present else "(skin_pack).mcpack"
        zip_path = os.path.join(output_folder, f"{skin_pack_name} {ext}")
        if os.path.exists(zip_path):
            base, e = os.path.splitext(zip_path)
            count = 1
            while os.path.exists(zip_path):
                zip_path = f"{base}_{count}{e}"
                count += 1
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, _, files in os.walk(pack_folder):
                for file in files:
                    fp = os.path.join(root, file)
                    zipf.write(fp, os.path.relpath(fp, pack_folder))


def dlc_main(extracted_folders, keys_file, output_folder, is_addon=False):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
    if not isinstance(extracted_folders, list):
        extracted_folders = [extracted_folders]
    if isinstance(keys_file, str):
        keys_file = [keys_file]

    folders_to_compress = []
    pack_name = None

    for folder in extracted_folders:
        manifest_path = os.path.join(folder, "manifest.json")
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
        first_uuid = manifest.get("header", {}).get("uuid")
        if not first_uuid:
            continue
        key = dlc_get_key_from_tsv(keys_file, first_uuid)
        if not key:
            continue
        dlc_decrypt_files(folder, keys_file, first_uuid)
        dlc_modify_level_dat(folder)
        pack_name = dlc_get_pack_name(folder)
        folders_to_compress.append(folder)

    if folders_to_compress and pack_name:
        if is_addon:
            dlc_compress_files_zip(folders_to_compress, pack_name, output_folder, is_addon=True)
        else:
            for folder in folders_to_compress:
                dlc_compress_files_zip(folder, pack_name, output_folder, is_addon=False)


def extract_id_from_url(url):
    pattern = r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})'
    match = re.search(pattern, url, re.I)
    return match.group(1) if match else None


def load_settings(file_path="settings.json"):
    try:
        with open(file_path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def load_keys_from_files():
    loaded = []
    for fname in ["keys.tsv", "personal_keys.tsv"]:
        try:
            with open(fname, "r") as f:
                loaded.extend(f.readlines())
        except FileNotFoundError:
            pass
    return loaded


def check_custom_id(custom_ids, loaded_lines):
    if isinstance(custom_ids, str):
        custom_ids = {custom_ids}
    elif isinstance(custom_ids, list):
        custom_ids = set(custom_ids)
    for line in loaded_lines:
        for cid in custom_ids:
            if cid in line:
                return True
    return False


def _log_error(first_uuid, e):
    msg = f"Error processing pack: {first_uuid} - Error: {str(e)}" if first_uuid else f"Error: {str(e)}"
    print(msg)
    with open('error_log.txt', 'a') as f:
        f.write(msg + '\n')


def download_and_process_zip(zip_url, output_folder, retries=3, timeout=160):
    for attempt in range(retries):
        try:
            response = requests.get(
                zip_url, timeout=timeout,
                headers={"User-Agent": "libhttpclient/1.0.0.0"}, stream=True
            )
            response.raise_for_status()

            total_size = int(response.headers.get('content-length', 0))
            downloaded_size = 0
            zip_filename = zip_url.split("/")[-1]
            random_folder = uuid_module.uuid4().hex
            pack_folder = os.path.join(output_folder, random_folder)
            os.makedirs(pack_folder, exist_ok=True)

            zip_path = os.path.join(pack_folder, zip_filename)
            with open(zip_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded_size += len(chunk)

            extracted = []
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for name in zf.namelist():
                    if name.endswith('.zip'):
                        nested_path = os.path.join(pack_folder, name)
                        nested_out = os.path.join(pack_folder, os.path.splitext(name)[0])
                        os.makedirs(nested_out, exist_ok=True)
                        zf.extract(name, pack_folder)
                        with zipfile.ZipFile(nested_path, 'r') as nzf:
                            nzf.extractall(nested_out)
                        os.remove(nested_path)
                        extracted.append((os.path.splitext(name)[0], nested_out))

            os.remove(zip_path)
            return extracted

        except (zipfile.BadZipFile,):
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
            else:
                return None
    return None


def check_for_addon(folder_path):
    manifest_path = os.path.join(folder_path, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r') as f:
            data = json.load(f)
        return data.get("metadata", {}).get("product_type") == "addon"
    return False


def data_uuid(folder_path):
    manifest_path = os.path.join(folder_path, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path, 'r') as f:
            data = json.load(f)
        return data.get("header", {}).get("uuid")
    return None


auth_token = None


def auth_login():
    global auth_token
    try:
        response = pf_login_with_custom_id()
        if 'PlayFabId' in response:
            auth_token = pf_get_entity_token(response['PlayFabId'], 'master_player_account')
            return True
    except Exception as e:
        print(f"Login failed: {e}")
    return False


def perform_search(query, orderBy, select, top, skip, search_type, search_term):
    global auth_token
    if not auth_token:
        if not auth_login():
            return None
    try:
        return pf_search_name(
            query=query, order_by=orderBy, select=select,
            top=top, skip=skip, search_type=search_type, search_term=search_term
        )
    except Exception as e:
        if 'Unauthorized' in str(e):
            if auth_login():
                return pf_search_name(
                    query=query, order_by=orderBy, select=select,
                    top=top, skip=skip, search_type=search_type, search_term=search_term
                )
        raise


PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_settings()
    global auth_token
    if not auth_token:
        if not auth_login():
            print("Warning: Failed to authenticate at startup")
    yield


app = FastAPI(
    title="FreeCraft API",
    description="Free Minecraft Marketplace Content Search & Download",
    version="2.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

API_KEYS = {
    hashlib.sha256("freecraft_key_2025".encode()).hexdigest(): "internal",
    hashlib.sha256("xoid_mc_key_2024".encode()).hexdigest(): "internal"
}

download_rate_limit: Dict[str, float] = {}
active_downloads: Dict[str, list] = {}
download_lock = Lock()
DOWNLOAD_RATE_LIMIT_SECONDS = 5
MAX_CONCURRENT_DOWNLOADS_PER_USER = 3
thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=10)


class SearchRequest(BaseModel):
    query: str
    search_type: Optional[str] = "name"
    limit: Optional[int] = 50


class SearchResponse(BaseModel):
    success: bool
    data: List[Dict[str, Any]]
    total: int
    query: str
    search_type: str


class DownloadRequest(BaseModel):
    item_id: str
    process_content: Optional[bool] = False


def verify_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="API key required")
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    if token_hash not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return API_KEYS[token_hash]


def search_local_data(query, search_type="name", limit=50):
    results = []
    list_path = os.path.join(os.path.dirname(__file__), "list.txt")
    if not os.path.exists(list_path):
        return results
    try:
        with open(list_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    parts = line.rsplit(' - ', 1)
                    if len(parts) != 2:
                        continue
                    title_creator = parts[0].strip()
                    type_uuid = parts[1].strip()
                    type_parts = type_uuid.split(' ', 1)
                    if len(type_parts) != 2:
                        continue
                    content_type = type_parts[0].strip()
                    item_uuid = type_parts[1].strip()
                    if ' ( ' in title_creator and title_creator.endswith(' )'):
                        title_end = title_creator.rfind(' ( ')
                        title = title_creator[:title_end].strip()
                        creator = title_creator[title_end + 3:-2].strip()
                    else:
                        title = title_creator
                        creator = "Unknown"
                    if query.lower() in title.lower():
                        results.append({
                            "Id": item_uuid,
                            "Title": {"en-US": title},
                            "DisplayProperties": {"creatorName": creator},
                            "ContentType": ["MarketplaceDurableCatalog_V1.2"],
                            "Tags": [content_type.lower()],
                            "source": "local"
                        })
                        if len(results) >= limit:
                            break
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        print(f"Error searching local data: {e}")
    return results


def enrich_local_results_with_images(results):
    if not results:
        return results
    try:
        item_ids = [r["Id"] for r in results]
        playfab_data = playfab_main(item_ids)
        if playfab_data:
            for result in results:
                if result["Id"] in playfab_data:
                    result["Images"] = playfab_data[result["Id"]].get("Images", [])
    except Exception as e:
        print(f"Error enriching results: {e}")
    return results


@lru_cache(maxsize=100)
def cached_search(query: str, search_type: str, limit: int):
    global auth_token
    try:
        if "id=" in query or "minecraft.net" in query:
            extracted_id = extract_id_from_url(query)
            if extracted_id:
                result = playfab_main([extracted_id])
                items = list(result.values()) if result else []
                return {"success": True, "data": items, "total": len(items), "query": query, "search_type": "uuid"}

        elif re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', query.strip()):
            result = playfab_main([query.strip()])
            items = list(result.values()) if result else []
            return {"success": True, "data": items, "total": len(items), "query": query, "search_type": "uuid"}

        else:
            try:
                data = perform_search(
                    query="", orderBy="creationDate DESC", select="contents,images",
                    top=min(limit, 300), skip=0, search_term=query, search_type=search_type
                )
                items = data.get("Items", []) if isinstance(data, dict) else (data or [])
                if query and search_type == "name":
                    items = [i for i in items if all(
                        t in i.get("Title", {}).get("en-US", "").lower()
                        for t in query.lower().split()
                    )]
                return {"success": True, "data": items[:limit], "total": len(items), "query": query, "search_type": search_type}

            except Exception as pf_err:
                print(f"PlayFab search failed, using local: {pf_err}")
                local = search_local_data(query, search_type, limit)
                enriched = enrich_local_results_with_images(local)
                return {"success": True, "data": enriched, "total": len(enriched), "query": query,
                        "search_type": search_type, "source": "local_fallback"}

    except Exception as e:
        try:
            local = search_local_data(query, search_type, limit)
            enriched = enrich_local_results_with_images(local)
            return {"success": True, "data": enriched, "total": len(enriched), "query": query,
                    "search_type": search_type, "source": "local_emergency_fallback"}
        except:
            raise HTTPException(status_code=500, detail=f"All search methods failed: {str(e)}")


def check_download_rate_limit(user_id: str) -> bool:
    with download_lock:
        now = time.time()
        if user_id not in active_downloads:
            active_downloads[user_id] = []
        active_downloads[user_id] = [t for t in active_downloads[user_id]
                                      if now - t < DOWNLOAD_RATE_LIMIT_SECONDS]
        if len(active_downloads[user_id]) >= MAX_CONCURRENT_DOWNLOADS_PER_USER:
            return False
        active_downloads[user_id].append(now)
        return True


def release_download_slot(user_id: str, start_time: float):
    with download_lock:
        if user_id in active_downloads:
            try:
                active_downloads[user_id].remove(start_time)
            except ValueError:
                pass


async def get_download_info_from_playfab(item_id: str):
    try:
        result = playfab_main([item_id])
        if not result or item_id not in result:
            raise HTTPException(status_code=404, detail="Content not found")

        item = result[item_id]
        title = item.get("Title", {}).get("en-US", "Unknown")
        contents = item.get("Contents", [])
        if not contents:
            raise HTTPException(status_code=404, detail="No downloadable content found")

        content_info = {
            "title": title, "content_types": [], "playfab_content_types": [],
            "playfab_contents": contents, "total_files": len(contents), "has_multiple_types": False
        }

        for c in contents:
            ct = c.get("Type", "")
            if ct:
                content_info["playfab_content_types"].append(ct)

        for tag in item.get("Tags", []):
            tl = tag.lower()
            if "skin" in tl:
                content_info["content_types"].append("Skin Pack")
            elif "resource" in tl or "texture" in tl:
                content_info["content_types"].append("Resource Pack")
            elif "addon" in tl or "behavior" in tl:
                content_info["content_types"].append("Add-On")
            elif "world" in tl or "map" in tl:
                content_info["content_types"].append("World")
            elif "mashup" in tl:
                content_info["content_types"].append("Mashup Pack")

        for ct in content_info["playfab_content_types"]:
            if ct in {"skinbinary", "personabinary"} and "Skin Pack" not in content_info["content_types"]:
                content_info["content_types"].append("Skin Pack")

        if not content_info["content_types"]:
            content_info["content_types"].append("Mixed Content" if len(contents) > 1 else "Content Pack")

        content_info["has_multiple_types"] = len(content_info["content_types"]) > 1 or len(contents) > 1

        download_url = next((c["Url"] for c in contents if "Url" in c), None)
        if not download_url:
            raise HTTPException(status_code=404, detail="No download URL found")

        ct_str = " + ".join(content_info["content_types"])
        safe_title = re.sub(r'[<>:"/\\|?*]', '_', title.replace(' ', '_'))
        filename = f"{safe_title}_({ct_str}).zip" if content_info["has_multiple_types"] else f"{safe_title}.zip"

        return download_url, filename, content_info

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get download info: {str(e)}")


def process_content_sync(item_id: str, download_url: str, title: str, content_info: dict):
    temp_dir = tempfile.mkdtemp(prefix="freecraft_tmp_")
    dl_folder = os.path.join(temp_dir, "download")
    out_folder = os.path.join(temp_dir, "output")
    os.makedirs(dl_folder, exist_ok=True)
    os.makedirs(out_folder, exist_ok=True)

    try:
        loaded_keys = load_keys_from_files()
        has_key = check_custom_id(item_id, loaded_keys)
        skin_urls, other_urls = [], []

        for c in content_info.get("playfab_contents", []):
            ct = c.get("Type", "")
            if ct in {"skinbinary", "personabinary"}:
                skin_urls.append(c["Url"])
            else:
                other_urls.append(c["Url"])

        processed_files = []

        for url in other_urls + skin_urls:
            extracted = download_and_process_zip(url, dl_folder)
            if not extracted:
                continue

            is_skin = url in skin_urls
            if is_skin:
                for _, pack_folder in extracted:
                    try:
                        first_uuid = data_uuid(pack_folder)
                        dlc_skin_main(pack_folder, out_folder)
                        for f in os.listdir(out_folder):
                            if f.endswith(('.mcpack', '.zip')) and f not in processed_files:
                                processed_files.append(f)
                    except Exception as e:
                        _log_error(first_uuid if 'first_uuid' in locals() else None, e)
            else:
                addon_folders, dlc_folders = [], []
                for _, pack_folder in extracted:
                    (addon_folders if check_for_addon(pack_folder) else dlc_folders).append(pack_folder)

                if addon_folders:
                    try:
                        dlc_main(addon_folders, ["keys.tsv", "personal_keys.tsv"], out_folder, is_addon=True)
                        for f in os.listdir(out_folder):
                            if f.endswith(('.mcaddon', '.mcpack')) and f not in processed_files:
                                processed_files.append(f)
                    except Exception as e:
                        _log_error(None, e)

                if dlc_folders:
                    try:
                        dlc_main(dlc_folders, ["keys.tsv", "personal_keys.tsv"], out_folder, is_addon=False)
                        for f in os.listdir(out_folder):
                            if f.endswith(('.mcpack', '.mctemplate')) and f not in processed_files:
                                processed_files.append(f)
                    except Exception as e:
                        _log_error(None, e)

        if not processed_files:
            if not has_key and not skin_urls:
                raise HTTPException(status_code=422, detail={
                    "error": "missing_decryption_keys",
                    "message": "This content requires decryption keys that are not available.",
                    "title": title, "item_id": item_id
                })
            raise Exception(f"No content files were processed for '{title}'")

        if len(processed_files) > 1:
            combined = f"{title.replace(' ', '_')}_content.zip"
            combined_path = os.path.join(out_folder, combined)
            with zipfile.ZipFile(combined_path, 'w') as zf:
                for fn in processed_files:
                    fp = os.path.join(out_folder, fn)
                    if os.path.exists(fp):
                        zf.write(fp, fn)
            return combined, combined_path
        else:
            fn = processed_files[0]
            return fn, os.path.join(out_folder, fn)

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Content processing failed: {str(e)}")
    finally:
        shutil.rmtree(dl_folder, ignore_errors=True)


async def process_content(item_id, download_url, title, content_info):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(thread_pool, process_content_sync,
                                      item_id, download_url, title, content_info)


def stream_from_url(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": "libhttpclient/1.0.0.0"}, stream=True, timeout=60)
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Download failed: {str(e)}")


def stream_from_file(file_path: str):
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            yield chunk


@app.get("/api/status")
async def api_status():
    return {"message": "FreeCraft API v2.0", "status": "online"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "version": "2.0", "service": "FreeCraft"}


@app.get("/api/browse")
async def browse_content(
    category: str = Query("newest"),
    limit: int = Query(24),
    skip: int = Query(0),
    api_user: str = Depends(verify_api_key)
):
    valid_cats = ["name", "texture", "mashup", "addon", "persona", "skin", "newest"]
    if category not in valid_cats:
        category = "newest"
    limit = max(1, min(limit, 100))
    try:
        data = perform_search(
            query="", orderBy="creationDate DESC", select="contents,images",
            top=limit, skip=skip, search_term="", search_type=category
        )
        items = data.get("Items", []) if isinstance(data, dict) else (data or [])
        return {"success": True, "data": items[:limit], "total": len(items),
                "category": category, "skip": skip, "limit": limit}
    except Exception as e:
        local = search_local_data("", "name", limit)
        return {"success": True, "data": local, "total": len(local),
                "category": category, "skip": skip, "limit": limit, "source": "local_fallback"}


@app.post("/api/search", response_model=SearchResponse)
async def search_content(request: SearchRequest, api_user: str = Depends(verify_api_key)):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
    valid_types = ["name", "texture", "mashup", "addon", "persona", "capes", "hidden", "skin", "newest"]
    if request.search_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid search type. Must be one of: {valid_types}")
    try:
        result = cached_search(request.query, request.search_type, request.limit or 50)
        return SearchResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/search")
async def search_get(
    q: str = Query(...),
    type: str = Query("name"),
    limit: int = Query(50),
    api_user: str = Depends(verify_api_key)
):
    return await search_content(SearchRequest(query=q, search_type=type, limit=limit), api_user)


@app.post("/api/download")
async def download_content(request: DownloadRequest, api_user: str = Depends(verify_api_key)):
    start_time = time.time()
    if not check_download_rate_limit(api_user):
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")

    try:
        download_url, filename, content_info = await get_download_info_from_playfab(request.item_id)

        if request.process_content:
            proc_filename, file_path = await process_content(
                request.item_id, download_url, content_info["title"], content_info)
            ext = os.path.splitext(proc_filename)[1]
            media_type = "application/zip" if ext == ".zip" else "application/octet-stream"
            file_size = os.path.getsize(file_path)

            def file_streamer():
                try:
                    yield from stream_from_file(file_path)
                finally:
                    shutil.rmtree(os.path.dirname(file_path), ignore_errors=True)
                    release_download_slot(api_user, start_time)

            return StreamingResponse(file_streamer(), media_type=media_type, headers={
                "Content-Disposition": f'attachment; filename="{proc_filename}"',
                "Content-Length": str(file_size),
                "X-Processed": "true"
            })
        else:
            head = requests.head(download_url, headers={"User-Agent": "libhttpclient/1.0.0.0"}, timeout=30)
            resp_headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Processed": "false"}
            if cl := head.headers.get('content-length'):
                resp_headers["Content-Length"] = cl

            def raw_streamer():
                try:
                    yield from stream_from_url(download_url)
                finally:
                    release_download_slot(api_user, start_time)

            return StreamingResponse(raw_streamer(), media_type="application/zip", headers=resp_headers)

    except HTTPException:
        release_download_slot(api_user, start_time)
        raise
    except Exception as e:
        release_download_slot(api_user, start_time)
        raise HTTPException(status_code=500, detail=str(e))


if os.path.exists(PUBLIC_DIR):
    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", os.environ.get("API_PORT", 8000)))
    uvicorn.run(app, host="0.0.0.0", port=port)
