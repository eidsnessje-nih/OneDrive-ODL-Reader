"""
(c) 2021 Yogesh Khatri, @SwiftForensics 

Read OneDrive .ODL files
------------------------
OneDrive logs are stored as binary files with extensions .odl,
.odlgz, .odlsent and .aold usually found in the profile folder of 
a user under the following paths on Windows :
\AppData\Local\Microsoft\OneDrive\logs\Business1
\AppData\Local\Microsoft\OneDrive\logs\Personal

On macOS, they will usually be under:
/Users/<USER>/Library/Logs/OneDrive/Business1
/Users/<USER>/Library/Logs/OneDrive/Personal
/Users/<USER>/Library/Logs/OneDrive/Common

Author  : Yogesh Khatri, yogesh@swiftforensics.com
License : MIT
Version : 1.2, 2022-02-13
Usage   : odl.py [-o OUTPUT_PATH] [-k] [-d] [-s obfuscationmap.txt] odl_folder
          odl_folder is the path to folder where .odl and .odlgz
          are stored. OUTPUT_PATH is optional, if not
          specified, output will be saved in odl_folder. When
          extracting these files from an image, also extract the 
          "ObfuscationStringMap.txt" file and put it in the odl_folder
          which is its default location, or ideally save the entire
          folder. Usually there is only one ObfuscationStringMap file
          present in either the Business1 or Personal folder and .odl 
          files in other folders use it too. 

Requires python3.7+ and the construct module
"""

import argparse
import csv
import datetime
import glob
import gzip
import io
import os
import re
import string
import struct

from construct import *
from construct.core import Int32ul, Int64ul

control_chars = ''.join(map(chr, range(0,32))) + ''.join(map(chr, range(127,160)))
not_control_char_re = re.compile(f'[^{control_chars}]' + '{4,}')
# If  we only want ascii, use 'ascii_chars_re' below
printable_chars_for_re = string.printable.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
ascii_chars_re = re.compile(f'[{printable_chars_for_re}]' + '{4,}')


def ReadUnixMsTime(unix_time_ms): # Unix millisecond timestamp
    '''Returns datetime object, or empty string upon error'''
    if unix_time_ms not in ( 0, None, ''):
        try:
            if isinstance(unix_time_ms, str):
                unix_time_ms = float(unix_time_ms)
            return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=unix_time_ms/1000)
        except (ValueError, OverflowError, TypeError) as ex:
            #print("ReadUnixMsTime() Failed to convert timestamp from value " + str(unix_time_ms) + " Error was: " + str(ex))
            pass
    return ''

CDEF = Struct(
    "signature" / Int64ul, # CCDDEEFF00000000
    "timestamp" / Int64ul,
    "unk1" / Int32ul,
    "unk2" / Int32ul,
    "unknown" / Byte[20],
    "one" / Int32ul,      # 1
    "data_len" / Int32ul,
    "reserved" / Int32ul  # 0
    # followed by Data
)

Odl_header = Struct(
    "signature" / Int64ul, # EBFGONED
    "unk_version" / Int32ul,
    "unknown_2" / Int32ul,
    "unknown_3" / Int64ul,
    "unknown_4" / Int32ul,
    "one_drive_version" / Byte[0x40],
    "windows_version" / Byte[0x40],
    "reserved" / Byte[0x64]
)

def read_string(data):
    '''read string, return tuple (bytes_consumed, string)'''
    if (len(data)) >= 4:
        str_len = struct.unpack('<I', data[0:4])[0]
        if str_len:
            if str_len > len(data):
                print("Error in read_string()")
            else:
                return (4 + str_len, data[4:4 + str_len].decode('utf8', 'ignore'))
    return (4, '')

def guess_encoding(obfuscation_map_path):
    '''Returns either UTF8 or UTF16LE after checking the file'''
    encoding = 'utf-16le' # on windows this is the default
    with open(obfuscation_map_path, 'rb') as f:
        data = f.read(4)
        if len(data) == 4: 
            if data[1] == 0 and data[3] == 0 and data[0] != 0 and data[2] != 0:
                pass # confirmed utf-16le
            else:
                encoding = 'utf8'
    return encoding


def read_obfuscation_map(obfuscation_map_path, store_all_key_values):
    map = {}
    repeated_items_found = False
    encoding = guess_encoding(obfuscation_map_path)
    with open(obfuscation_map_path, 'r', encoding=encoding) as f:
        for line in f.readlines():
            line = line.rstrip('\n')
            terms = line.split('\t')
            if len(terms) == 2:
                if terms[0] in map: #REPEATED item found!  
                    repeated_items_found = True
                    if not store_all_key_values:
                        continue # newer items are on top, skip older items found below.
                    old_val = map[terms[0]]
                    new_val = f'{old_val}|{terms[1]}'
                    map[terms[0]] = new_val
                    last_key = terms[0]
                    last_val = new_val
                else:
                    map[terms[0]] = terms[1]
                    last_key = terms[0]
                    last_val = terms[1]
            else:
                if terms[0] in map:
                    if not store_all_key_values:
                        continue
                last_val += '\n' + line
                map[last_key] = last_val
                #print('Error? ' + str(terms))
    if repeated_items_found:
        print('WARNING: Multiple instances of some keys were found in the ObfuscationMap.')
    return map
    
def tokenized_replace(string, map):
    output = ''
    tokens = ':\\.@%#&*-+=|{}!?<>;:~()//"\''
    parts = [] # [ ('word', 1), (':', 0), ..] word=1, token=0
    last_word = ''
    last_token = ''
    for i, char in enumerate(string):
        if char in tokens:
            if last_word:
                parts.append((last_word, 1))
                last_word = ''
            if last_token:
                last_token += char
            else:
                last_token = char
        else:
            if last_token:
                parts.append((last_token, 0))
                last_token = ''
            if last_word:
                last_word += char
            else:
                last_word = char
    if last_token:
        parts.append((last_token, 0))
    if last_word:
        parts.append((last_word, 1))
    
    # now join all parts replacing the words
    for part in parts:
        if part[1] == 0: # token
            output += part[0]
        else: # word
            word = part[0]
            if word in map:
                output += map[word]
            else:
                output += word
    return output

def extract_strings(data, map):
    extracted = []
    #for match in not_control_char_re.finditer(data): # This gets all unicode chars, can include lot of garbage if you only care about English, will miss out other languages
    for match in ascii_chars_re.finditer(data): # Matches ONLY Ascii (old behavior) , good if you only care about English
        x = match.group().rstrip('\n').rstrip('\r')
        x.replace('\r', '').replace('\n', ' ')
        x = tokenized_replace(x, map)
        extracted.append(x)

    if len(extracted) == 0:
        extracted = ''
    elif len(extracted) == 1:
        extracted = extracted[0]
    return extracted

def process_odl(path, map, show_all_data):
    odl_rows = []
    basename = os.path.basename(path)
    with open(path, 'rb') as f:
        i = 1
        header = f.read(8)
        if header[0:8] == b'EBFGONED': # Odl header
            f.seek(0x100)
            header = f.read(8)
            file_pos = 0x108
        else:
            file_pos = 8
        # Now either we have the gzip header here or the CDEF header (compressed or uncompressed handles both)
        if header[0:4] == b'\x1F\x8B\x08\x00': # gzip
            try:
                f.seek(file_pos - 8)
                file_data = gzip.decompress(f.read())
            except (gzip.BadGzipFile,OSError) as ex:
                print('..decompression error for file {path} ' + str(ex))
                return
            f.close()
            f = io.BytesIO(file_data)
            header = f.read(8)
        if header != b'\xCC\xDD\xEE\xFF\0\0\0\0': # CDEF header
            print('wrong header! Did not find 0xCCDDEEFF')
            return
        else:
            f.seek(-8, io.SEEK_CUR)
            header = f.read(56) # odl complete header is 56 bytes
        while header:
            odl = {
                'Filename' : basename,
                'File_Index' : i,
                'Timestamp' : None,
                'Code_File' : '',
                'Function' : '',
                'Params_Decoded' : ''
            }
            header = CDEF.parse(header)
            timestamp = ReadUnixMsTime(header.timestamp)
            odl['Timestamp'] = timestamp
            data = f.read(header.data_len)
            data_pos, code_file_name = read_string(data)
            flags = struct.unpack('<I', data[data_pos : data_pos + 4])[0]
            data_pos += 4
            temp_pos, code_function_name = read_string(data[data_pos:])
            data_pos += temp_pos
            if data_pos < header.data_len:
                params = data[data_pos:]
                try:
                    params = params.decode('utf8', 'ignore')
                except Exception as ex:
                    print(ex)
                try:
                    #strings = extract_strings(params, {})
                    strings_decoded = extract_strings(params, map)
                    #print(strings)
                except Exception as ex:
                    print(ex)
            else:
                strings_decoded = ''
            #odl['Params'] = strings
            odl['Code_File'] = code_file_name
            odl['Function'] = code_function_name
            odl['Params_Decoded'] = strings_decoded
            #print(basename, i, timestamp, code_file_name, code_function_name, strings)
            if show_all_data:
                odl_rows.append(odl)
            else: # filter out irrelevant
                # cache.cpp Find function provides no value, as search term or result is not present
                if code_function_name == 'Find' and odl['Code_File'] == 'cache.cpp':
                    pass
                elif code_function_name == 'RecordCallTimeTaken' and odl['Code_File'] == 'AclHelper.cpp':
                    pass
                elif code_function_name == 'UpdateSyncStatusText' and odl['Code_File'] == 'ActivityCenterHeaderModel.cpp':
                    pass
                elif code_function_name == 'FireEvent' and odl['Code_File'] == 'EventMachine.cpp':
                    pass
                elif odl['Code_File'] in ('LogUploader2.cpp', 'LogUploader.cpp', 'ServerRefreshState.cpp', 'SyncTelemetry.cpp'):
                    pass
                elif strings_decoded == '':
                    pass
                else:
                    odl_rows.append(odl)
            i += 1
            file_pos += header.data_len
            header = f.read(56) # next cdef header
    return odl_rows

def main():
    usage = \
    """
(c) 2021 Yogesh Khatri,  @swiftforensics
This script will read OneDrive sync logs. These logs are produced by OneDrive, 
and are stored in a binary format having the extensions .odl .odlgz .oldsent .aold

Sometimes the ObfuscationMap stores old and new values of Keys. By default, only 
the latest value is fetched. Use -k option to get all possible values (values will 
be | delimited). 

By default, irrelevant functions and/or those with empty parameters are not displayed.
This can be toggled with the -d option.
    """

    parser = argparse.ArgumentParser(description='OneDrive Log (ODL) reader', epilog=usage, 
                formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('odl_folder', help='Path to folder with .odl files')
    parser.add_argument('-o', '--output_path', help='Output file name and path')
    parser.add_argument('-s', '--obfuscationstringmap_path', help='Path to ObfuscationStringMap.txt (if not in odl_folder)')
    parser.add_argument('-k', '--all_key_values', action='store_true', help='For repeated keys in ObfuscationMap, get all values | delimited (off by default)')
    parser.add_argument('-d', '--all_data', action='store_true', help='Show all data (off by default)')
    
    args = parser.parse_args()

    odl_folder = os.path.abspath(args.odl_folder)
    csv_file_path = args.output_path

    if not os.path.isdir(odl_folder):
        print(f'Error, {odl_folder} is not a folder!')
        return
    if not csv_file_path:
        csv_file_path = os.path.join(odl_folder, 'ODL_Report.csv')
    elif not csv_file_path.endswith('.csv'):
        csv_file_path += '.csv'

    if args.obfuscationstringmap_path:
        obfuscation_map_path = args.obfuscationstringmap_path
    else:
        obfuscation_map_path = os.path.join(odl_folder, "ObfuscationStringMap.txt")

    if not os.path.exists(obfuscation_map_path):
        print(f'"ObfuscationStringMap.txt" not found in {odl_folder}. Cannot proceed!')
        print('Please specify the path to ObfuscationStringMap.txt (with -s option), it may be in either the Business1 or Personal folder')
        return
    else:
        map = read_obfuscation_map(obfuscation_map_path, args.all_key_values)
        print(f'Read {len(map)} items from map')

    try:
        fieldnames = 'Filename,File_Index,Timestamp,Code_File,Function,Params_Decoded'.split(',')
        csv_f = open(csv_file_path, 'w', encoding='UTF8')
        writer = csv.DictWriter(csv_f, fieldnames=fieldnames)
        writer.writeheader()
    except:
        print(f"Failed to create csv file: {csv_file_path} ")
        return

    glob_patterns = ('*.odl', '*.odlgz', '*.odlsent', '*.aodl')
    paths = []
    for pattern in glob_patterns:
        paths.extend(glob.glob(os.path.join(odl_folder, pattern)))
    for path in paths:
        print("Searching ", path)
        try:
            odl_rows = process_odl(path, map, args.all_data)
            try:
                writer.writerows(odl_rows)
                print(f'Wrote {len(odl_rows)} rows')
            except Exception as ex:
                print("ERROR writing rows:", type(ex), ex)
        except OSError as ex:
            print(f"Error - File not found! {path}")
        
    csv_f.close()
    print(f'Finished processing files, output is at {csv_file_path}')

if __name__ == "__main__":
    main()
