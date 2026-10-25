"""PREV_ICCD24 - baseline mapper (the previous work, ICCD 2024), as used for the comparison in the TCAD 2026 paper.

  python3 mappers/PREV_ICCD24.py -i benchmarks/netlists/ISCAS85/c432.v -c 64x64
"""
import re
import sys
#from collections import OrdereDict
import os
import re
import time

global Attempt
global Cycle
global lut_max_V
global current_level_luts

# Home directory of the current user
home_dir = os.path.expanduser("~")

# Path setup
# ---- command line ----
import argparse as _argparse
import os as _os
_METHOD = "PREV_ICCD24"
_parser = _argparse.ArgumentParser(description="PREV_ICCD24: technology mapping of a NOR-of-NOR LUT netlist onto a memristor crossbar")
_parser.add_argument("-i", "--input", required=True, help="NoN LUT netlist (.v), e.g. benchmarks/netlists/ISCAS85/c432.v")
_parser.add_argument("-c", "--crossbar", required=True, help="crossbar size RxC, e.g. 64x64")
_parser.add_argument("-o", "--out", default=None, help="output directory (default: results/<bench>_<RxC>_<method>)")
_parser.add_argument("--threads", type=int, default=4, help="Gurobi threads (default 4); not used by this mapper")
_args = _parser.parse_args()
_cb_rows, _cb_cols = (int(x) for x in _args.crossbar.lower().split("x"))
_bench_name = _os.path.splitext(_os.path.basename(_args.input))[0].replace("_mapped_named", "")
_suffix = ""
_RESULT_DIR = _os.path.abspath(_args.out or _os.path.join("results", f"{_bench_name}_{_cb_rows}x{_cb_cols}_{_METHOD}{_suffix}"))
_os.makedirs(_RESULT_DIR, exist_ok=True)


def _report():
    """One summary line on the terminal when the run ends (the detailed log is in log.txt)."""
    import re as _re
    import sys as _sys
    try:
        _last = open(_os.path.join(_RESULT_DIR, "instructions.txt"), encoding="utf-8").read().rstrip().splitlines()[-1]
    except (OSError, IndexError):
        _last = ""
    if _last.startswith("Place Cycle"):
        _total = sum(int(v) for v in _re.findall(r"\d+", _last))
        print(f"[{_METHOD}] {_bench_name} {_cb_rows}x{_cb_cols}: {_total} cycles ({_last.strip()}) -> {_RESULT_DIR}", file=_sys.__stdout__)
    else:
        print(f"[{_METHOD}] {_bench_name} {_cb_rows}x{_cb_cols}: mapping failed (see {_RESULT_DIR}/log.txt)", file=_sys.__stdout__)


import atexit as _atexit
_atexit.register(_report)
# ---- end command line ----
Path = _RESULT_DIR
Path2 = _RESULT_DIR

Attempt = f"{_bench_name}_{_cb_rows}x{_cb_cols}_{_METHOD}"
Cycle = 1
VNOR_Cycle = 0
HNOR_Cycle = 0
A_star_Cycle = 0
Place_Cycle = 0
Copy_IR_Cycle = 0
Reset_Cycle = 0
lut_max_V = 0
current_level_luts = []

import time

# Per-function runtime records
runtimes = {}
section_times = {}


import time
from functools import wraps

import sys
# Open the log file
log_file_path = _os.path.join(Path, "log.txt")
log = open(log_file_path, 'w', encoding='utf-8', buffering=1)  # line-buffered

# Redirect both stdout and stderr to the log file
sys.stdout = log
sys.stderr = log

def timer(func):
    name = func.__name__
    runtimes[name] = {"count": 0, "total_time": 0.0}

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start

        runtimes[name]["count"] += 1
        runtimes[name]["total_time"] += elapsed
        return result

    return wrapper

def log_section_time(section_name, start_time):
    elapsed = time.time() - start_time
    if section_name not in section_times:
        section_times[section_name] = []
    section_times[section_name].append(elapsed)




####### Initial parsing ######
def parse_verilog(verilog_str):         
    global Path
    global lut_max_V
    assigns = re.findall(r'assign\s+~(.*?)\s+=\s+(.*?);', verilog_str)
    luts = {}
    print("Parsing result:", assigns)

    # Build the file path
    file_path = os.path.join(Path, "instructions.txt")

    # Create the directory if it does not exist
    os.makedirs(Path, exist_ok=True)

    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            data = "Final output\n"
            f.write(data)
        print(f"File saved: {file_path}")

    except Exception as e:
        print(f"Failed to save file: {e}")


    for output, expr in assigns:
        fanin = set(re.findall(r'\b\w+\b', expr)) - {output} 
        if '&' not in expr:
            product_terms = 1
        else: product_terms = len(expr.split('|'))   
        
        faninlen = len(fanin)
        if output in luts:
            print(f"Error: Multiple assignments for {output}")
            return 
        luts[output] = LUT(name=output, fanin=fanin, product_terms=product_terms, size=[faninlen + 1, product_terms + 1], level=555, expr=expr, row=[99], col=[99], fanlevel = 0)
        if lut_max_V < product_terms + 1: 
            lut_max_V = product_terms + 1        
    return luts


######level, fanlevel, size optimization#######
def update_lut_info(luts):              
    for lut_name, lut in luts.items():
        width = len(lut.fanin) + 1
        height = lut.product_terms + 1
        
    for lut_name, lut in luts.items():
        k = 0
        for idx, r in enumerate(luts[lut_name].fanin):
            if r[0] != 'I':
                k = 10
        if k == 0: 
            luts[lut_name].level = 1
        elif k == 10:
            luts[lut_name].level = 555
        else: luts[lut_name].level = 25

    _asap = {}
    for _root in luts:
        _stack = [_root]
        while _stack:
            _x = _stack[-1]
            if _x in _asap:
                _stack.pop()
                continue
            _todo = [f for f in luts[_x].fanin if f in luts and f != _x and f not in _asap]
            if _todo:
                _stack.extend(_todo)
                continue
            _asap[_x] = 1 + max([_asap[f] for f in luts[_x].fanin if f in luts and f != _x] + [0])
            _stack.pop()
    for lut_name in luts:
        luts[lut_name].level = _asap[lut_name]


    for idx, lut in enumerate(luts):
        for chek_lut in luts:
            if lut in luts[chek_lut].fanin:                
                luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)


    for lut in luts:
        if luts[lut].size[1] > 2:
            terms_before = luts[lut].expr.replace('(', '').replace(')','')
            terms = []
            temp =[]
            terms = terms_before.split('|')
            for idx, r in enumerate(range(luts[lut].size[1])):
                terms_elements = terms[idx].split('&') if idx < len(terms) else []  
                temp.append(len(terms_elements))
            luts[lut].size[0] = max(temp) + 1

    return luts

######2x2   3x2   4x2   5x2
######2x3   3x3   4x3   5x3
######2x4   3x4   4x4   5x4
######2x5   3x5   4x5   5x5
######Collect size information for block-style placement######


def Optimizing_luts(luts):
    global lut_max_V          
    ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
    max_level = ordered_luts[-1].level
    mapping_info = [[[0 for _ in range(4)]for _ in range(lut_max_V)]for _ in range(max_level)]  # 3-D list: one (V x H) size-count grid per level

    for lut in ordered_luts:
        level = lut.level - 1
        row = lut.size[1] -2
        col = lut.size[0] -2
        mapping_info[level][row][col] += 1   #index note: level+1 = level, row+2 = height (V), col+2 = width (H)

    return mapping_info    

class LUT:
    def __init__(self, name, fanin, product_terms, size, level, expr, row, col, fanlevel):
        self.name = name
        self.fanin = fanin
        self.product_terms = product_terms
        self.size = size
        self.level = level
        self.expr = expr
        self.row = row
        self.col = col
        self.fanlevel = fanlevel
        
    def __repr__(self): 
        return f"LUT(name={self.name}, fanin={self.fanin}, product_terms={self.product_terms}, size={self.size}, level={self.level}, expr={self.expr}, row={self.row}, col={self.col}, fanlevel={self.fanlevel})"

def initialize_crossbar(rows, cols):

    return Crossbar(rows, cols)




#######Crossbar allocation, lut mapping#######
#######Crossbar allocation, lut mapping#######
class Crossbar:
    def __init__(self, rows, cols):
        self.grid = [[None for _ in range(cols)] for _ in range(rows)]

    def __str__(self):
        return '\n'.join([' '.join(["%-8s" % cell if cell else '    .   ' for cell in row]) for row in self.grid])

    def __repr__(self):
        return self.__str__() 
    
    #######Final lut mapping#######
    @timer
    def map_lut(self, lut, luts, area_VH):
        global Cycle
        global Attempt
        global Path
        
        can_c, can_r = self.can_place(lut, area_VH)

        for i in can_c:
            for j in can_r:
                #can_c = self.can_place(i, j, lut, luts)
                if self.grid[j][i] is None:
                    this_c=can_c
                    for c in can_c:
                        if c < i:
                            this_c.remove(c)

                    temp = self.place_lut(can_r, this_c, lut)
                    if temp == False: return False
                    return True 
                else: False
                if not can_c and j == len(self.grid)-1 and i == len(self.grid[0])-1:
                    return False
        return False


#######Whether the LUT can be placed#######
    def can_place(self, lut, area_VH):
        global lut_max_V  
        size = lut.size
        can_c = []
        can_r =[] 
        temp_r=0
        temp_c=0
        for row in range(lut_max_V-1):
            if row+2 < int(size[1]):
                    temp_r+=int(area_VH["V"+str(row+2)])
            elif row+2 == int(size[1]):
                if int(area_VH["V"+str(row+2)]) == 0: ###############ROW 1
                    for n in range(temp_r):
                        can_r.append(temp_r+n)
                else:
                    for n in range(int(area_VH["V"+str(row+2)])):
                        can_r.append(temp_r+n)


        for col in range(4):
            if col+2 < int(size[0]):
                temp_c+=int(area_VH["H"+str(col+2)])
            elif col+2 == int(size[0]):
                if int(area_VH["H"+str(col+2)]) == 0:
                    for n in range(temp_c):
                        can_c.append(temp_c+n)
                else:
                    for n in range(int(area_VH["H"+str(col+2)])):
                        can_c.append(temp_c+n)

        return can_c, can_r
###############
    def optimize_can_c(self, can_r, this_c, lut):
        can_c=[]
        flag=0
        for c in this_c:
            flag=0
            for r in can_r:
                if all((0 <= c+i < len(self.grid[0])) and (self.grid[r][c+i] is None) for i in range(lut.size[0])):
                    flag+=1
                    if flag == (int(lut.size[1])):
                        can_c=[c+n for n in range(int(lut.size[0]))]
                        this_r = r - (lut.size[1]-1)
                        return can_c, this_r 
        return [], 0

#######Place LUT + fanin#######
        # ===== Helpers: put these ABOVE place_lut =====
    @staticmethod
    def _unwrap_parens(s: str) -> str:
        """Strip redundant outer parentheses enclosing the whole string (nested ones too)."""
        s = s.strip()
        while s.startswith("(") and s.endswith(")"):
            depth = 0
            ok = True
            for i, ch in enumerate(s):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and i != len(s) - 1:
                        ok = False
                        break
            if ok:
                s = s[1:-1].strip()
            else:
                break
        return s

    @staticmethod
    def _fix_parens(s: str) -> str:
        """Fix unbalanced opening/closing parentheses."""
        depth = 0
        fixed = []
        for ch in s:
            if ch == "(":
                depth += 1
                fixed.append(ch)
            elif ch == ")":
                if depth > 0:
                    depth -= 1
                    fixed.append(ch)
                else:
                    # ignore an unmatched )
                    continue
            else:
                fixed.append(ch)
        # close any remaining open parentheses
        fixed.extend(")" * depth)
        return "".join(fixed)

    @staticmethod
    def split_top(expr: str, op: str):
        """Split on op ('&' or '|') only at the top level, respecting parenthesis depth."""
        res = []
        buf = ""
        depth = 0
        for ch in expr:
            if ch == "(":
                depth += 1
                buf += ch
            elif ch == ")":
                depth -= 1
                buf += ch
            elif ch == op and depth == 0:
                res.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf:
            res.append(buf.strip())
        return res

    @staticmethod
    def parse_expr(expr: str):
        """
        Parse a Verilog logic expression (AND/OR/NOT only) → {negated: bool, terms: list[list[str]]}
        - terms: list split on OR; each item is a list split on AND
        - each operand is returned without parentheses or spaces (e.g. 'IG77', '~IG77', 'n83')
        e.g. ~( (A & B) | (C & D) )
            -> {'negated': True, 'terms': [['A','B'], ['C','D']]}
        """
        expr = Crossbar._fix_parens(expr.strip())      # balance the parentheses first
        expr = Crossbar._unwrap_parens(expr)

        # detect negation of the whole expression ~(...)
        is_negated = False
        if expr.startswith("~(") and expr.endswith(")"):
            is_negated = True
            expr = Crossbar._fix_parens(expr[1:].strip())
            expr = Crossbar._unwrap_parens(expr)

        # split on top-level OR (a single term if there is no OR)
        or_terms = Crossbar.split_top(expr, '|')

        parsed_terms = []
        for t in or_terms:
            t = Crossbar._unwrap_parens(t)
            and_terms = Crossbar.split_top(t, '&')  # a single operand if there is no AND
            cleaned = [Crossbar._unwrap_parens(x).replace(" ", "") for x in and_terms if x.strip() != ""]
            parsed_terms.append(cleaned)

        return {"negated": is_negated, "terms": parsed_terms}

    def place_lut(self, can_r, this_c, lut):
        global Cycle, Place_Cycle, Attempt, Path

        # --- 1) parse expression properly (use parse_expr, not naive replace) ---
        parsed = Crossbar.parse_expr(lut.expr)   # {'negated': bool, 'terms': [['~IG1'], ['~IG13','~IG41','IG20']]}
        or_terms = parsed.get("terms", [])

        # --- 2) pick columns/row using your existing optimizer ---
        can_c, row = self.optimize_can_c(can_r, this_c, lut)
        if not can_c:
            return False

        # logging (kept same as before)
        for r in range(lut.size[1] - 1):
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a') as f:
                data = f"C{Cycle} Place LUT{lut.name} ({row + r}, {can_c[0]}), ({row + r}, {can_c[-1]}) \n"
                f.write(data)
                Cycle += 1
                Place_Cycle += 1

        # --- 3) place tokens into grid using parsed tokens (no manual '(' remove) ---
        temp_row = []
        temp_col = []

        for idx, r in enumerate(range(lut.size[1])):
            # for each row, take the corresponding OR-term (list of AND tokens) if exists
            if idx < len(or_terms):
                term_elements = or_terms[idx]        # already cleaned tokens like '~IG1', 'IG20', 'n83'
            else:
                term_elements = []

            if row + r not in temp_row:
                temp_row.append(row + r)

            for idx2, c in enumerate(can_c):
                # bounds check (use >=, not >)
                if row + r >= len(self.grid) or c >= len(self.grid[0]):
                    return False

                if c not in temp_col:
                    temp_col.append(c)

                if idx2 < len(term_elements):
                    value = term_elements[idx2].strip()
                    # value already includes '~' if it's negated; no double-~ will be present
                    self.grid[row + r][c] = f"{lut.name}_{value}"

                elif idx2 == lut.size[0] - 1:
                    if r == lut.size[1] - 1:
                        self.grid[row + r][c] = f"{lut.name}_{lut.name}" if parsed.get("negated") else f"{lut.name}_~{lut.name}"
                    else:
                        self.grid[row + r][c] = f"{lut.name}_0"

                elif r == lut.size[1] - 1:
                    self.grid[row + r][c] = None
                else:
                    self.grid[row + r][c] = f"{lut.name}_0"

        # write the cell labels of the placed rows as a comment line,
        # read by verify/replay.py; not an instruction, so cycle counts are unaffected
        with open(os.path.join(Path, "instructions.txt"), 'a') as _f:
            for _r in range(lut.size[1] - 1):
                _labs = []
                for _c in can_c:
                    _v = self.grid[row + _r][_c]
                    _labs.append(_v.split('_', 1)[1] if _v and '_' in _v else '0')
                _f.write(f"#LABELS LUT{lut.name} ({row + _r}, {can_c[0]}), ({row + _r}, {can_c[-1]}) cols {','.join(map(str, can_c))} | {' '.join(_labs)}\n")
        lut.row = temp_row
        lut.col = temp_col
        return True    

#######A* definitions#######
"""
def g(r, c, Rd, Cd, start_polarity, destination_polarity, crossbar):
    if isinstance(destination_polarity, str):
        if (r == Rd or c == Cd):
            destination_polarity = 1-start_polarity
        elif (r != Rd and c != Cd):
            destination_polarity = start_polarity

    if (r, c) == (Rd, Cd):
        return 0, destination_polarity
    elif (r == Rd or c == Cd) and (start_polarity != destination_polarity):
        return 1, destination_polarity
    
    elif (r == Rd or c == Cd) and (start_polarity == destination_polarity):
        return 2, destination_polarity
    
    elif (r != Rd and c != Cd) and (start_polarity == destination_polarity):
        if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
            return 2, destination_polarity
        else: return 4, destination_polarity
    

    elif (r != Rd and c != Cd) and (start_polarity != destination_polarity):
        #if crossbar.grid[Rd][c] or crossbar.grid[r][Cd] == None:
            return 3, destination_polarity
        #else: return 5, destination_polarity
        """

def g(r, c, Rd, Cd, start_polarity, destination_polarity, crossbar):
    if isinstance(destination_polarity, str):
        if (r == Rd or c == Cd):
            destination_polarity = 1-start_polarity
        elif (r != Rd and c != Cd):
            destination_polarity = start_polarity

    if (r, c) == (Rd, Cd):
        return 0, destination_polarity
    elif (r == Rd or c == Cd): #and (start_polarity != destination_polarity):
        return 1, destination_polarity
    else: return 2, destination_polarity

def get_neighbors(r, c, Rd, Cd, crossbar, source_polarity, destination_polarity, source, A_star_idx):
    neighbors = []
    #col_neighbor = crossbar.grid[Rd][Cd].split("_")[1]
    #if col_neighbor[0] == "~":
     #   col_neighbor[1:]
    #if crossbar.grid[r][c] is not None:
     #   if "_" in crossbar.grid[r][c]:
      #      IR = crossbar.grid[r][c].split("_")[1]
       #     if IR[0] == "~":
        #        IR = IR[1:]

    if  c == Cd:       
        #if A_star_idx == 1 and (r,c) == source and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[1][-2:] == crossbar.grid[r][c].split("_")[0][-2:] or 
        if crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = even
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid)):
                        if crossbar.grid[i][c] is None and i != r:
                            neighbors.append((i,Cd))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
    if r == Rd:
        #if A_star_idx == 1 and (r,c) == source and "_" in crossbar.grid[r][c] and crossbar.grid[r][c].split("_")[1][-2:] ==  crossbar.grid[r][c].split("_")[0][-2:]or 
        if crossbar.grid[r][c] is None:
            if source_polarity != destination_polarity: #total path length = odd
                if A_star_idx % 2 == 1:
                    neighbors.append((Rd,Cd))
                    return neighbors
                else: 
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
            else:
                if A_star_idx % 2 == 1:
                    for i in range(len(crossbar.grid[0])):
                        if crossbar.grid[r][i] is None and i != c:
                            neighbors.append((Rd,i))
                            return neighbors
                else: 
                    neighbors.append((Rd,Cd))
                    return neighbors
                
    if crossbar.grid[r][Cd] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((r,Cd))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[r][i] is None and i != c:
                        neighbors.append((r,i))
                        return neighbors
            else: 
                neighbors.append((r,Cd))
                return neighbors
            
    if crossbar.grid[Rd][c] is None:
        if source_polarity == destination_polarity: #total path length = odd
            if A_star_idx % 2 == 1:
                neighbors.append((Rd,c))
                return neighbors
            else: 
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
        else:
            if A_star_idx % 2 == 1:
                for i in range(len(crossbar.grid[0])):
                    if crossbar.grid[i][c] is None and i != r:
                        neighbors.append((i,c))
                        return neighbors
            else: 
                neighbors.append((Rd,c))
                return neighbors

    neighbors2 = []
    if A_star_idx % 2 == 1:
        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                if crossbar.grid[Rd][i] is None or crossbar.grid[r][Cd] is None:
                    neighbors.append((r, i))
                    neighbors2.append((r, i))
                else:
                    neighbors2.append((r, i))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:
                neighbors2.append((i, c))
    else:
        for i in range(len(crossbar.grid)):
            if crossbar.grid[i][c] is None:

                if crossbar.grid[Rd][c] is None or crossbar.grid[i][Cd] is None:
                    neighbors.append((i, c))
                    neighbors2.append((i, c))
                else:
                    neighbors2.append((i, c))

        if len (neighbors) >= 10: 
            return neighbors

        for i in range(len(crossbar.grid[0])):
            if crossbar.grid[r][i] is None:
                neighbors2.append((r,i))

    return neighbors2

import time, heapq
from functools import lru_cache
def A_star_search(source, destination, source_polarity, destination_polarity, crossbar):
    """
    Optimized version:
      - encodes the state (r,c,pol) as an int
      - minimizes heapq operations (bulk neighbor push + heapify)
      - the heuristic fallback is an inline Manhattan distance
    """
    global place_info_dict
    Rs, Cs = source
    Rd, Cd = destination
    R, C = len(crossbar.grid), len(crossbar.grid[0])

    # -------- State encode/decode --------
    def encode_state(r, c, pol):
        return ((r * C) + c) << 1 | pol

    def decode_state(state_id):
        pol = state_id & 1
        idx = state_id >> 1
        r, c = divmod(idx, C)
        return r, c, pol

    # -------- Neighbor cache --------
    @lru_cache(maxsize=None)
    def cached_neighbors(r, c, path_rev):
        raw = get_neighbors(r, c, Rd, Cd, crossbar,source_polarity, destination_polarity, source, path_rev)
        seen = set()
        cleaned = []
        for nr, nc in raw:
            if (nr, nc) == (r, c):
                continue
            if (nr, nc) not in seen:
                seen.add((nr, nc))
                cleaned.append((nr, nc))
        return tuple(cleaned)

    # -------- Heuristic --------
    if 'g' in globals():
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            h, _ = g(nr, nc, Rd, Cd, pol, destination_polarity, crossbar)
            return h
    else:
        @lru_cache(maxsize=None)
        def heuristic(nr, nc, pol):
            return abs(nr - Rd) + abs(nc - Cd)

    # -------- Path reconstruction --------
    def reconstruct_path(came_from, end_state_id):
        path_rev = []
        cur = end_state_id
        while cur is not None:
            r, c, pol = decode_state(cur)
            path_rev.append(((r, c), pol))
            cur = came_from.get(cur, None)
        path_rev.reverse()
        return path_rev

    # -------- Init --------
    log_section_time("A_star_search::section1 init", time.time())
    open_heap = []
    start_id = encode_state(Rs, Cs, source_polarity)
    start_h = heuristic(Rs, Cs, source_polarity)
    heapq.heappush(open_heap, (start_h, 0, start_id))

    closed_set = set()
    came_from = {start_id: None}
    gscore = {start_id: 0}

    # -------- Search --------
    while open_heap:
        t0 = time.time()
        priority, gcur, state_id = heapq.heappop(open_heap)
        log_section_time("A_star_search::section2-1", t0)

        if state_id in closed_set:
            continue
        closed_set.add(state_id)

        r, c, pol = decode_state(state_id)

        # goal check
        if (r, c) == (Rd, Cd):
            same_pol = (source_polarity == destination_polarity)
            if (same_pol and (gcur % 2 == 0)) or ((not same_pol) and (gcur % 2 == 1)):
                path_rev = reconstruct_path(came_from, state_id)
                return path_rev
        #else: path_rev = []
        # expand neighbors (optimization: heappush only)
        t3 = time.time()
        path_len = gscore[state_id] + 1
        for nr, nc in cached_neighbors(r, c, path_len):
            new_pol = 1 - pol
            nstate = encode_state(nr, nc, new_pol)
            if nstate in closed_set:
                continue

            tentative_g = gcur + 1
            prev_g = gscore.get(nstate)
            if prev_g is None or tentative_g < prev_g:
                gscore[nstate] = tentative_g
                came_from[nstate] = state_id
                h = heuristic(nr, nc, new_pol)
                f = tentative_g + h
                heapq.heappush(open_heap, (f, tentative_g, nstate))
        log_section_time("A_star_search::section6", t3)

#######Find a fanin, find its sources, and map the A* route#######

def mapping_A_star(path, lut, crossbar, destination_polarity, source_polarity):
    #crossbar = mapping_A_star(path, fanin, crossbar, opt_destination_polarity, start_polarity)
    global Attempt
    global Cycle
    global Path
    global A_star_Cycle
    
    if path is None:
        return crossbar 
    

    for idx, ((r, c),_) in enumerate(path):
        #if idx == 0 and '_' not in crossbar.grid[r][c]:
        #    if crossbar.grid[r][c][0] == "~": 
        #        source_polarity = 0   #0:~ 1:as is

        #    else: 
        #        source_polarity = 1

        if idx == 0:
            if "_" in crossbar.grid[r][c]: 
                even_source = crossbar.grid[r][c].split("_")[1]
                # a literal cell (not an IR) labelled L holds NOT L; the route labels must
                # describe held values, as they do when the route starts at an IR
                _p0, _p1 = crossbar.grid[r][c].split("_", 1)
                if _p1 not in ("0",) and _p0.lstrip("~") != _p1.lstrip("~"):
                    even_source = even_source[1:] if even_source.startswith("~") else "~" + even_source
            else:
                even_source = crossbar.grid[r][c]

            if even_source[0] == "~":
                odd_source = even_source[1:]
            else:
                odd_source = "~" + even_source



        if idx > 0 and idx < len(path):
            check1 = int(path[idx-1][0][0])
            check2 = int(path[idx-1][0][1])
            # append the copy instruction
            file_path = os.path.join(Path, "instructions.txt")
            with open(file_path, 'a', encoding='utf-8') as f:
                data = "C%d Copy LUT%s (%d, %d) to (%d, %d)\n" % (Cycle, lut, check1, check2, r, c)
                f.write(data)
                Cycle += 1  # increment Cycle after writing
                A_star_Cycle += 1

        

        if idx >= len(path)-1: return crossbar
        #edit ~=source polarity=0

        elif idx > 0 and  idx < len(path) - 1 and idx % 2 == 0:  #even idx
                crossbar.grid[r][c] = even_source                

        elif idx > 0 and  idx < len(path) - 1 and idx % 2 == 1: #odd idx
            #if source_polarity == 0: ##~n
             #   lut1=source[1:]
                crossbar.grid[r][c] = odd_source
            #else:
             #   crossbar.grid[r][c] = "~"+source
        
    return crossbar
    
def find_fanin(row, col, luts, crossbar, cell_content):
    for r in range(len(crossbar.grid)):
        for c in range(len(crossbar.grid[0])):
            if row+r > len(crossbar.grid) - 1 or col+c > len(crossbar.grid[0]) - 1 : break
            elif crossbar.grid[row+r][col+c] is not None and '_' in crossbar.grid[row+r][col+c]:

                test_temp = crossbar.grid[row+r][col+c].split('_')
                if len(test_temp) < 2: continue

                cell_content = test_temp[0]
                cell_content2 = test_temp[1]

                if col+c != luts[cell_content].col[-1]:

                    if crossbar.grid[row+r][col+c].split('_')[0] != crossbar.grid[row+r][col+c].split('_')[1] or cell_content != cell_content2[1:]:
                    
                        #if '_' not in crossbar.grid[row+r][col+c] : continue
                        
                        if len(cell_content) > 0 and len(cell_content2) > 1:  # skip one-character labels such as '0'
                            if cell_content2[0] == "n" or cell_content2[0] == "O" or cell_content2[0:2] == "~n" or cell_content2[0:2] == "~O" : #c != luts[cell_content].col[-1] and 
                                current_level = luts[cell_content].level
                                fanin = crossbar.grid[row+r][col+c].split('_')[1] #fanin: fanin name, i.e. the name at the destination
                                if crossbar.grid[row+r][col+c].split('_')[1][0] == "~": 
                                    destination_polarity = 1  # a '~x' cell must hold x
                                    if cell_content2[0] == "~":
                                        print(cell_content2)
                                else: 
                                    destination_polarity = 0  # an 'x' cell must hold NOT x
                                #print(target) 
                                sources=find_sources((row+r,col+c), fanin, current_level, luts, crossbar, row, col)
                                return (row+r,col+c),fanin, destination_polarity, sources, cell_content #target: the lut that appears as a fanin, excluding intermediate results
                                                                    #row+r, col+c  == destination
        col=0    

def find_sources(location, fanin, current_level, luts, crossbar, row, col): 
    sources=[]  
    for r in range(len(crossbar.grid)):
        for c in range(len(crossbar.grid[0])):
            if (r,c) == location: continue # r,c = destination
            elif crossbar.grid[r][c] is not None:  
                #destination = crossbar.grid[row][col].split
                #if destination 

                if '_' in crossbar.grid[r][c]:
                #if fanin[0] == "~" and (crossbar.grid[r][c][-4:] == (fanin or fanin[1:]) or crossbar.grid[r][c][-3:] == fanin[1:]) or crossbar.grid[r][c][-3:] == fanin or crossbar.grid[r][c][-4:] == fanin or crossbar.grid[r][c][-5:] == fanin: 
                    #if fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin[1:]) or fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1] == fanin):
                            if fanin[0] != "~" and (crossbar.grid[r][c].split("_")[1]== "~" + fanin or crossbar.grid[r][c].split("_")[1]== fanin) or fanin[0] == "~" and (crossbar.grid[r][c].split("_")[1] == fanin or crossbar.grid[r][c].split("_")[1] == fanin[1:]):
                                cell_content = crossbar.grid[r][c].split('_')[0]
                                if cell_content[0] == "~":
                                    cell_content = cell_content[1:]
                            
                                cell_content2 = crossbar.grid[r][c].split("_")[1]
                                if cell_content2[0] == "~":
                                    cell_content2 = cell_content2[1:]


                                if cell_content == cell_content2: #saved IR    
                                    if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                        source_polarity = 0   #0:~ 1:
                                        sources.append(((r,c),fanin, source_polarity))

                                    else: 
                                        source_polarity = 1
                                        sources.append(((r,c),fanin, source_polarity))
                                else: #placed
                                    if luts[cell_content].level == current_level and (location[0] >= r and location[1] > c) :
                                        if crossbar.grid[r][c].split('_')[1][0] == "~": 
                                            source_polarity = 1   # a '~x' literal cell holds x
                                            sources.append(((r,c),fanin, source_polarity))

                                        else: 
                                            source_polarity = 0   # an 'x' literal cell holds NOT x
                                            sources.append(((r,c),fanin, source_polarity))           


                else: # '_' not in crossbar.grid[r][c] = A*
                    if fanin[0] != "~" and (crossbar.grid[r][c]== "~" + fanin or crossbar.grid[r][c]== fanin) or fanin[0] == "~" and (crossbar.grid[r][c] == fanin or crossbar.grid[r][c] == fanin[1:]):
                        if crossbar.grid[r][c][0] == "~": 
                            source_polarity = 0   #0:~ 1:as is
                            sources.append(((r,c),fanin, source_polarity))

                        else: 
                            source_polarity = 1
                            sources.append(((r,c),fanin, source_polarity))  #((row,col), lut name, source_polarity)
    return sources
 


def _exact_copy_route(source, destination, source_polarity, destination_polarity, crossbar):
    """Complete search, used only when A* finds no path from
    any source. Same move model as get_neighbors: one copy (MAGIC NOT) moves a value to any empty cell
    in the same row or column and flips its polarity; the destination is the fan-in cell. Breadth-first
    search over (cell, move parity) returns a route with the fewest copies whose parity matches the
    required polarity, in A_star_search's format [((r, c), polarity), ...], or None if none exists."""
    from collections import deque
    grid = crossbar.grid
    R, C = len(grid), len(grid[0])
    Rs, Cs = source
    Rd, Cd = destination
    need = 0 if source_polarity == destination_polarity else 1
    start = (Rs, Cs, 0)
    prev = {start: None}
    dq = deque([start])
    while dq:
        r, c, par = dq.popleft()
        npar = 1 - par
        cand = [(r, j) for j in range(C) if j != c] + [(i, c) for i in range(R) if i != r]
        for nr, nc in cand:
            if (nr, nc) == (Rd, Cd):
                if npar == need:
                    route = [(nr, nc, npar)]
                    cur = (r, c, par)
                    while cur is not None:
                        route.append(cur)
                        cur = prev[cur]
                    route.reverse()
                    return [((rr, cc), source_polarity ^ pp) for rr, cc, pp in route]
                continue
            if grid[nr][nc] is None:
                st = (nr, nc, npar)
                if st not in prev:
                    prev[st] = (r, c, par)
                    dq.append(st)
    return None


def _rescue_route(fanin, destination, destination_polarity, crossbar, sources, routed):
    """Called only when A* finds no path from any source (the run
    would otherwise stop with A*_search_Fail). First a complete search, then reset the dead copies
    left on the route cells of fan-ins routed earlier in this level (their fan-in cells already hold the value)
    one group at a time (one cycle each: the row, then the column of each source, then of the fan-in cell, then
    every column), searching again after each reset. LUT cells, stored IRs ('_' cells), routed fan-in cells and
    copies of this net are kept. Returns the path, or None if no route exists even after all resets."""
    global Cycle, Reset_Cycle
    grid = crossbar.grid
    R, C = len(grid), len(grid[0])
    net = fanin.lstrip("~")
    holders = [(s[0], s[2]) for s in sources]

    def route():
        for (r, c), pol in holders:
            p = _exact_copy_route((r, c), destination, pol, destination_polarity, crossbar)
            if p:
                return p
        return None

    path = route()
    if path:
        print(f"[route rescue] complete search: route for {fanin} -> {destination} without reset")
        return path
    groups = []
    for (r, c), _ in holders + [(destination, destination_polarity)]:
        groups += [("r=%d" % r, [(r, j) for j in range(C)]), ("c=%d" % c, [(i, c) for i in range(R)])]
    groups += [("c=%d" % c, [(i, c) for i in range(R)]) for c in range(C)]
    n_resets = 0
    for label, cells in groups:
        cells = [(r, c) for r, c in cells if grid[r][c] is not None and "_" not in grid[r][c]
                 and (r, c) not in routed and grid[r][c].lstrip("~") != net]
        if not cells:
            continue
        for r, c in cells:
            grid[r][c] = None
        with open(_os.path.join(Path, "instructions.txt"), "a") as f:
            f.write(f"C{Cycle} Reset Route ({label}): {', '.join(f'({r}, {c})' for r, c in cells)}\n")
        Cycle += 1
        Reset_Cycle += 1
        n_resets += 1
        path = route()
        if path:
            print(f"[route rescue] {fanin} -> {destination} after {n_resets} reset cycle(s)")
            return path
    print(f"[route rescue] failed: {fanin} -> {destination} ({n_resets} reset cycle(s))")
    return None


@timer
def execute_Copy(luts, crossbar): 
    _routed = set()  # fan-in cells routed so far; the route rescue keeps them
    row=0 
    col=0
    cell_content =""
    while True:
        result = []
        result = find_fanin(row, col, luts, crossbar,cell_content)
        

        if result is None:
            break 

        (row, col), fanin, destination_polarity, sources, cell_content = result  
        start_polarity = 1

        destination = row, col

        path=(0,0)
        

        shortest_path =None
        shortest_path_len = float('inf')
        
        for idx,source in enumerate(sources):  #sources : ((row,col), lut name, source_polarity)
            start = source[0] #source position
            start_polarity = source[2]
            path = A_star_search(start, destination, start_polarity, destination_polarity, crossbar)#,source[1])

            if path == []:#and shortest_path is not None:
                if len(sources) == 1 or (len(sources) > 1 and idx == len(sources) - 1  and shortest_path is None): 
                    pass  # crossbar print omitted to keep the log small
                    print('\n'+'\n')    
                    shortest_path = (0,0)
                    with open(_os.path.join(Path, "failure.txt"), "a") as f:
                        pass  # crossbar print omitted to keep the log small
                        print('\n'+'\n')
                        data ="A*_search_Fail: fanin = %s and lut = %s in level %d \n" % (fanin, cell_content, luts[cell_content].level)
                        f.write(data)
                        sys.exit()
            else:      
                if path and len(path) < shortest_path_len:
                    shortest_path = path
                    shortest_path_len = len(path)
        
        if shortest_path is None:  # A* found no path: fall back to the complete search
            for source in sources:
                path = _exact_copy_route(source[0], destination, source[2], destination_polarity, crossbar)
                if path and len(path) < shortest_path_len:
                    shortest_path = path
                    shortest_path_len = len(path)
            print(f"[routing] A* found no path for {fanin} -> {destination}; complete search: "
                  f"{'found ' + str(shortest_path_len - 1) + ' copies' if shortest_path else 'no route exists'}")
        if shortest_path is None and sources:  # still no path: reset dead copies and search again
            shortest_path = _rescue_route(fanin, destination, destination_polarity, crossbar, sources, _routed)
        if shortest_path is None:  # no source found, or no route exists
            with open(_os.path.join(Path, "failure.txt"), "a") as f:
                f.write("A*_search_Fail: fanin = %s and lut = %s in level %d (no path)\n" % (fanin, cell_content, luts[cell_content].level))
            sys.exit()
        crossbar = mapping_A_star(shortest_path, fanin, crossbar, destination_polarity, start_polarity)
        _routed.add(destination)  # mark this fan-in cell as routed
        col += 1
    
    return crossbar
    #######Main operations#######
#######reset, 
    

        


def init_SET(mapping_info):     #builds the SET from the initial luts
        global lut_max_V
        H = [2, 3, 4, 5]
        sizes = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]
        SET_Sequence = []   #list of the sizes (out of all 16) to map now
        
        for i in range(len(mapping_info)):
            for j in range(4):
                if mapping_info[i][j] != 0:
                    size = sizes[i][j]
                    if size not in SET_Sequence:
                        SET_Sequence.append(size)
        return SET_Sequence

def compute_count_size(mapping_info, SET_Sequence):
    global lut_max_V
    H = [2, 3, 4, 5]
    keys_size = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]
    
    count_size={}  

    for i in range(len(mapping_info)):
        for j in range(4):
            if mapping_info[i][j] != 0:
                if keys_size[i][j] in SET_Sequence:
                    count_size[keys_size[i][j]] = mapping_info[i][j]        
    
    return count_size



def Folding_This(sorted_V_list, crossbar, count_size, area_VH):
    global lut_max_V
    new_sorted_V_list = []
    current_V2 = 0
    current_H2 = 0
    V_difference = [] 
    count_size2 = count_size.copy() 
    last_c=0

    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        if all(cell is None for cell in [row[c] for row in crossbar.grid]):
            last_c = c
            break

    for n in range(4):
        current_H2 += int(area_VH["H" + str(n+2)])
    for nn in range(lut_max_V-1):
        current_V2 += int(area_VH["V" + str(nn+2)])  

    if current_V2 <= len(crossbar.grid) and current_H2 < last_c:
        # check that every count is below 5
        if all(count < 5 for count in count_size2.values()):
            return area_VH, None

    new_temp_V = cal_V_temp(count_size,area_VH)
    sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)
    
    while True:
        for (temp_V, value) in sorted_V_list:
            #if sorted_V_difference[0][0] == temp_V: # if sorted_V_difference[0][0] != temp_V it is not the largest difference, so pass
                for i in range (len(new_temp_V)):
                    if new_temp_V[i][0] == temp_V:
                        V_list = sorted(new_temp_V[i][1], reverse=True)
                        break

                folding_H = 0 
                for kkkey,cccount in count_size2.items():
                    if int(kkkey[1]) * int(cccount) == value:
                        folding_H = int(kkkey[0]) #H of the size being folded

                real_folding_H = 0
                for kkkkey,ccccount in area_VH.items():
                    if kkkkey == "H"+str(folding_H):
                        real_folding_H = ccccount #H including folding


                if len(sorted_V_difference) == 1:
                    if (int (value) % len(crossbar.grid)) == 0:
                        k = int(int (value) // len(crossbar.grid)) # k is the folding factor, e.g. 2 places the LUTs in two parts
                    else: 
                        k = int(int (value) // len(crossbar.grid)) +1
                else:
                    if len(sorted_V_difference) != 1: 
                        k = int(real_folding_H / folding_H) if folding_H else 0  # avoid division by zero when folding_H is 0
                        k+=1 #with several V sizes, raise the folding factor by one

                if k == 1: k+=1
                if k == 0: k+=2
                
                for j in range (len(sorted_V_list)):
                    if sorted_V_list[j][0] == temp_V:
                        V_max_i = int(sorted_V_list[j][1])
                        break
                
                i = -1
                current_V3 = 0
                for n in range(lut_max_V-1):
                    if (n+2) != int(temp_V[1]):
                        current_V3 += int(area_VH["V" + str(n+2)]) 

                if real_folding_H != folding_H: #already folded
                    while True: 
                    #max V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i*(k-1) // k) + 1
                        else: V_max_div = V_max_i*(k-1) // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = (V_max_div) + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether folding saves cycles
                        if k + V_max > k-1 + V_max_i: return area_VH,None

                        #check that the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None, None
                        else: break

                        #check that the width after folding fits in the grid 
                        only_H_list = []
                        sum_H = 0
                        for kkey,ccount in area_VH.items():
                            if kkey[0] == "H" and int(ccount) != 0:
                                only_H_list.append((kkey,ccount))

                        for i in range(len(only_H_list)):
                            sum_H += int(only_H_list[i][1]) 
                            
                        if len(sorted_V_difference) == 1:
                            if sum_H + folding_H*(k-1) < int(last_c):
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                        else:
                            if sum_H + folding_H < int(last_c): #fold only while staying inside the IR boundary
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                else:  #not folded yet
                    while True: 
                    #max V (height) after folding
                        if (V_max_i % k) != 0: V_max_div = (V_max_i // k) + 1
                        else: V_max_div = V_max_i // k

                        if (V_max_div) % (int(temp_V[1])) == 0:
                            V_max = V_max_div
                        else:
                            V_max = V_max_div + (int(temp_V[1]) - (V_max_div) % (int(temp_V[1]))) 

                        #2. whether folding saves cycles
                        if k + V_max > k-1 + V_max_i: return area_VH, None

                        #check that the height after folding fits in the grid
                        if current_V3 + V_max > int(len(crossbar.grid)):
                            k+=1
                            i+=1
                            if i == 1 and len(sorted_V_difference) == 1: return None,None
                        else: break

                        #check that the width after folding fits in the grid 
                        only_H_list = []
                        sum_H = 0
                        for kkey,ccount in area_VH.items():
                            if kkey[0] == "H" and int(ccount) != 0:
                                only_H_list.append((kkey,ccount))

                        for i in range(len(only_H_list)):
                            sum_H += int(only_H_list[i][1]) 
                            
                        if len(sorted_V_difference) == 1:
                            if sum_H + folding_H*(k-1) < int(last_c):
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None
                        else:
                            if sum_H + folding_H < int(last_c): #fold only while staying inside the IR boundary
                                area_VH["H"+str(folding_H)] = folding_H*k
                            else: return None, None

                #check that the width after folding fits in the grid 
                only_H_list = []
                sum_H = 0
                for kkey,ccount in area_VH.items():
                    if kkey[0] == "H" and int(ccount) != 0:
                        only_H_list.append((kkey,ccount))

                for i in range(len(only_H_list)):
                    sum_H += int(only_H_list[i][1]) 
                    
                if len(sorted_V_difference) == 1:
                    if sum_H + folding_H*(k-1) < int(last_c):
                        area_VH["H"+str(folding_H)] = folding_H*k
                    else: return None, None
                else:
                    if sum_H + folding_H < int(last_c): #fold only while staying inside the IR boundary
                        area_VH["H"+str(folding_H)] = folding_H*k
                    else: return None, None
               
                # after folding, also consider the second-largest V   
                if len(V_list) > 1: #two or more H sizes
                    if V_max < int(V_list[1]):
                        area_VH[temp_V] = int(V_list[1]) #######difference
                    else:
                        #sorted_V_list[0] = (sorted_V_list[0][0], V_max)
                        area_VH[temp_V] = V_max
                else:
                    #sorted_V_list[0] = (sorted_V_list[0][0], V_max)
                    area_VH[temp_V] = V_max

                
                for size2, cnt in count_size.items():
                    #area_VH_value = int(area_VH["V"+str(size2[1])])
                    if folding_H == size2[0]:
                        if cnt != 1 :
                            if cnt % k == 0:
                                count_size2[size2] = int(cnt // k)
                              #  if area_VH_value > int(cnt // k) * int(size2[1]):
                               #     area_VH_value = int(cnt // k) * int(size2[1])
                            else:
                                count_size2[size2] = int(cnt // k) + 1
                                #if area_VH_value > int((cnt // k)+1) * int(size2[1]):
                                 #   area_VH_value = int((cnt // k)+1) * int(size2[1])
                                 
                H = [2, 3, 4, 5]

                keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
                keys2 = [f"H{h}" for h in H]                    # widths

                #count_VH = {key: 0 for key in keys}

                for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
                    temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
                    for size, count in count_size2.items():
                        found = False
                        if(int(size[1]) == idx + 2):    
                            for i in range(len(temp_V)):
                                if temp_V[i] == 0 and not found:
                                    temp_V[i] = int(count)
                                    found = True
                                    break
                    
                    if temp_V and int(max(temp_V)) != 0:
                        for i in range(len(temp_V)):
                            temp_V[i] = int(temp_V[i]) * int(area_H[1])             
                        area_VH[area_H] = int(max(temp_V)) #area actually occupied, given the V size

                print(count_size2)
                     
                current_V = 0
                current_H = 0

                for n in range(4):
                    current_H += int(area_VH["H" + str(n+2)])
                for nn in range(lut_max_V-1):
                    current_V += int(area_VH["V" + str(nn+2)]) 

                if len(only_H_list) == 1: 
                    if current_V <= len(crossbar.grid) and current_H < last_c:
                        return area_VH, count_size2

                new_temp_V = cal_V_temp(count_size2,area_VH)
                sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)

                #print(area_VH)

                ##########debugging
                V_dict_temp={}
                #if  current_V > len(crossbar.grid) and current_H < int(last_c): ##fold again
                for key, value in area_VH.items():
                    if key[0] == "V":
                        V_dict_temp[key] = value
                new_sorted_V_list = sorted(V_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)
                #else: return None 

                #2. can it be folded further? 
                if new_sorted_V_list[0][1] + current_H > last_c:
                    return area_VH, count_size2
                    
                if new_sorted_V_list != sorted_V_list:
                    sorted_V_list = new_sorted_V_list
                    break







def define_dict(count_size, crossbar):    #builds the area_VH dictionary: area occupied per width/height\                                              #fold =0 no fold, fold==2 horizontal fold , fold ==3 vertical fold
    global lut_max_V
    #count_size={}       #lut count per size (out of all 16) to map now   
    #count_VH={}         #how many of widths 2,3,4,5 and heights 2,3,4,5 are used, initial value = 0
    area_VH={}          #rows/cols occupied by each of the 8 sizes (widths 2,3,4,5, heights 2,3,4,5) (not index)
    V_difference = []
    temp_global = 0      #area of the current set, compared with the whole crossbar  [width,height]
    sorted_temp_V = []
    sorted_V_difference = []

    H = [2, 3, 4, 5]
    keys_size = [[(h, v) for h in H] for v in range(2, lut_max_V + 1)]

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
    keys2 = [f"H{h}" for h in H]                    # widths
    #count_VH = {key: 0 for key in keys}
    area_VH = {key: 0 for key in keys2}
    area_VH.update({key: 0 for key in keys1})

    for size in count_size.keys():
        temp_global += int(size[1]) * int(size[0])
        area_VH["H" + str(size[0])] = int(size[0])
    for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
        temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
        for size, count in count_size.items():
            found = False
            if(int(size[1]) == idx + 2):    
                for i in range(len(temp_V)):
                    if temp_V[i] == 0 and not found:
                        temp_V[i] = int(count)
                        found = True
                        break
        
        if temp_V and int(max(temp_V)) != 0:
            for i in range(len(temp_V)):
                temp_V[i] = int(temp_V[i]) * int(area_H[1])             
            area_VH[area_H] = int(max(temp_V)) #area actually occupied, given the V size

            

    
    #if temp_global >= len(crossbar.grid[0])*len(crossbar.grid[1]) / 2 :  #run if the LUT area exceeds half of the crossbar
     #   This_VH =[0,0]
      #  for key, value in area_VH.items():
       #     if This_VH[1] < value:
        #        This_VH = key, value 

        #return {0: This_VH[0]}      # Dictionary 0: which size to remove, e.g. V3 
    
    return area_VH 
    #return count_size
    #return temp_global

@timer  
def cal_V_temp(count_size,area_VH):
    global lut_max_V
    new_temp_V = []
    H = [2, 3, 4, 5]

    keys1 = [f"V{v}" for v in range(2, lut_max_V + 1)]  # heights
    keys2 = [f"H{h}" for h in H]                    # widths
    #count_VH = {key: 0 for key in keys}

    used_H=[]

    for HH in keys2: 
        if area_VH.get(HH, 0) != 0:
            used_H.append(HH)
            
    for idx, area_H in enumerate(keys1):    #idx==0 -> compute H2
        temp_V = [0 for key in keys2 if area_VH.get(key, 0) != 0] ##len =1,2,3,4
        for size, count in count_size.items():
            found = False
            if(int(size[1]) == idx + 2):    
                for i in range(len(temp_V)):
                    if not found:
                        for idx2, H_idx in enumerate(used_H):
                            if (int(size[0]) == int(H_idx[1])):
                                temp_V[idx2] = int(count)
                                found = True
                                break
        
        if temp_V and int(max(temp_V)) != 0:
            for i in range(len(temp_V)):
                temp_V[i] = int(temp_V[i]) * int(area_H[1])             
            area_VH[area_H] = int(max(temp_V)) 
            new_temp_V.append((area_H,temp_V))
    
    temp = []
    for i in range(2,lut_max_V + 1):
        row_size = int(0)
        for size2,count2 in count_size.items():
            if size2[1] == i:
                if count2 > row_size:
                    row_size = count2
                    temp = size2
        
        for HV,count3 in area_VH.items():
            if HV[0] == "H" and int (count3) != int(0):
                col_size = int(count3)

        done = False
        for j in range (len(new_temp_V)):
            if done == True: break
            if int(new_temp_V[j][0][1]) == int(i):
                for HV,count3 in area_VH.items():
                    if HV[0] == "H" and int (count3) != int(0) and int(HV[1]) == int(temp[0]):
                        temp = new_temp_V[j] + (count3,)
                        new_temp_V.append(temp)
                        del new_temp_V[0]
                        done = True
                        break
        temp = []

    return new_temp_V

@timer    
def compute_reduce_space(V_difference, new_temp_V,area_VH):
    V_difference = []

    for label, values,max_H in new_temp_V:
        sorted_temp_V = sorted(values, reverse=True)

        if (len(sorted_temp_V) == 1): return [[label, sorted_temp_V[0]]]

        if sorted_temp_V[0] % 2 == 0:
            if sorted_temp_V and int((sorted_temp_V[0]//2)) >= int(sorted_temp_V[1]):
                V_difference.append((label,int(sorted_temp_V[0]//2)))
            else:
                V_difference.append((label,int(sorted_temp_V[0] - sorted_temp_V[1])))
        else:
            if sorted_temp_V and int((sorted_temp_V[0]//2)+1) >= int(sorted_temp_V[1]):
                V_difference.append((label,int(sorted_temp_V[0]//2)+1))
            else:
                V_difference.append((label,int(sorted_temp_V[0] - sorted_temp_V[1])))


    sorted_V_difference = sorted(V_difference, key=lambda x: x[1], reverse=True)
    return sorted_V_difference


def case_operate(SET_Sequence, crossbar, mapping_info, area_VH):
    global lut_max_V

    result_Sequence = SET_Sequence             #2-D list of the final Sequence
    result_Sequence = [result_Sequence]
    #This_Sequence = SET_Sequence                #Seq processed recursively
    case = 0
    extend_cnt = 0                      #number of SET extensions (number of resets)
    check_idx = 0      
    idx = 0                 #checked index

    while case<5:
        current_V = 0
        current_H = 0

        if idx == 0:
            count_size = {}
            count_size = compute_count_size(mapping_info, result_Sequence[check_idx])
            init_VH = {}
            V_difference = [] 
            count_size2 = count_size.copy()

        if idx == 1: 
            idx +=1

        if 0 not in area_VH:         #not 0 -> folding CASE
            V_dict_temp={}          #dictionary of the height (V) sizes
            for key, value in area_VH.items():
                if key[0] == "V":
                    V_dict_temp[key] = value
            sorted_V_list = sorted(V_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)     
                            ###height sizes sorted in descending order (as tuples)
            
            H_dict_temp={}          #dictionary of the width (H) sizes
            for key, value in area_VH.items():
                if key[0] == "H":
                    H_dict_temp[key] = value
            sorted_H_list = sorted(H_dict_temp.items(), key=lambda item: int(item[1]),reverse=True)  

            new_temp_V = cal_V_temp(count_size2,area_VH)
            sorted_V_difference = compute_reduce_space(V_difference, new_temp_V,area_VH)
            
            area_VH, count_size2 = Folding_This(sorted_V_list, crossbar, count_size2, area_VH)

            if area_VH is None:
                if len(sorted_V_list) <= 1 or sorted_V_list[1][1] == 0: #only one V remains
                    if sorted_H_list[1][1] == 0:
                        for key in V_dict_temp:
                            init_VH[key] = V_dict_temp[key]
                        for key in H_dict_temp:
                            init_VH[key] = H_dict_temp[key]
                        return None, init_VH
                    else:
                        return None, sorted(sorted_H_list[0][0])
                else:
                    return None, sorted(sorted_V_difference[0][0])
            
            for n in range(4):
                current_H += int(area_VH["H" + str(n+2)])
            for nn in range(lut_max_V-1):
                current_V += int(area_VH["V" + str(nn+2)]) 
                 
            if current_V <= len(crossbar.grid) and current_H < int(len(crossbar.grid[0])):  ##both within the crossbar size
                case = 7
                continue
            idx += 1
            
    return area_VH

@timer
def Blocking(mapping_info, crossbar):
    seq = init_SET(mapping_info)
    count_size = compute_count_size(mapping_info, seq)
    print (count_size)
    init_area_VH = define_dict( count_size, crossbar)
    result_area_VH = case_operate(seq, crossbar, mapping_info, init_area_VH)

    if None in result_area_VH:
        return result_area_VH[0], result_area_VH[1]
    return result_area_VH

#######NOR execution####### 
def execute_NOR(current_level, luts, crossbar):
    global Attempt
    global Cycle
    global Path
    global VNOR_Cycle
    global HNOR_Cycle
    global current_level_luts

    past_row_cells = []
    past_col_cells = []

    file_path = os.path.join(Path, "instructions.txt")

    rows = len(crossbar.grid)
    cols = len(crossbar.grid[0])

    for current_level_lut in current_level_luts:
        row_cells = []
        col_cells = []
        past_row_cells2 = []
        past_col_cells2 = []
    # ---- HNOR (row-wise NOR) ----
        Hdone = False
        for r in range(rows):
            # collect the cells of the current-level LUT in this row
            if Hdone == True: break
            for c in range(cols):
                if crossbar.grid[r][c] is not None and crossbar.grid[r][c].split("_")[0] == current_level_lut.name:
                    if c not in past_row_cells:
                        if c not in row_cells:
                            row_cells.append(c)


                if current_level_lut.size[0] == len(row_cells) and past_row_cells2 != row_cells:  # HNOR can run when 2 or more cells are present
                    with open(file_path, 'a', encoding='utf-8') as f:
                        row_cells.sort()
                        data = f"C{Cycle} HNOR col:{row_cells[:-1]} -> {row_cells[-1]}\n"
                        f.write(data)
                    Cycle += 1
                    HNOR_Cycle += 1
                    for rr in row_cells:
                        past_row_cells.append(rr) 
                        past_row_cells2.append(rr) 
                    Hdone = True

        # ---- VNOR (column-wise NOR) ---- and "_" in crossbar.grid[r][c]
    # VNORs are issued after every HNOR of the level (a VNOR acts on all columns of its rows)
    for current_level_lut in current_level_luts:
        col_cells = []
        past_col_cells2 = []
        Vdone = False
        for c in range(cols):
            if Vdone == True: break
            for r in range(rows):
                if crossbar.grid[r][c] is not None and crossbar.grid[r][c].split("_")[0] == current_level_lut.name:
                    if r not in past_col_cells:
                        if r not in col_cells:
                            col_cells.append(r)

                if current_level_lut.size[1] == len(col_cells) and past_col_cells2 != col_cells:  # VNOR can run when 2 or more cells are present
                    with open(file_path, 'a', encoding='utf-8') as f:
                        col_cells.sort()
                        data = f"C{Cycle} VNOR  col:{c}, row:{col_cells[:-1]} -> {col_cells[-1]}\n"
                        f.write(data)
                    Cycle += 1
                    VNOR_Cycle += 1
                    for rr in col_cells:
                        past_col_cells.append(rr) 
                        past_col_cells2.append(rr) 
                    Vdone = True

    return

"""
def original_luts(luts, current_level,lut_level_up):
    ##luts before level up
    original_this_luts = {}
    if lut_level_up == 0:
        for lut in luts:
            if luts[lut].level == current_level:
                    original_this_luts[lut] = luts[lut]
"""

@timer
def max_luts_in_one_V(max_v,V_num,H_num,crossbar):
    global lut_max_V
    max_luts=[]
    for c in range(len(crossbar.grid[0]) - 1, -1, -1):
        if all(cell is None for cell in [row[c] for row in crossbar.grid]):
            last_c = int(c)
        else: last_c = int(0.75*len(crossbar.grid[0]))

    for i in range (2,lut_max_V+1):
        if i == V_num:
            max_H = int(last_c / H_num)
            max_V = int(len(crossbar.grid) / i)
            max_luts = (i,int(max_H * max_V))

    for j in range(lut_max_V):
        if max_luts[j][0] == V_num:
            if max_v % max_luts[j][1] == 0:
                max_luts_num = max_v / max_luts[j][1]
            else: 
                max_luts_num = (max_v / max_luts[j][1]) + 1 
    return max_luts_num


@timer
def levelup_luts(luts, current_level, area_VH, lut_level_up, crossbar, ordered_luts):
    if isinstance(area_VH[1], list):
        this_luts = {}
        past_luts = {}
        num = int(area_VH[1][0])
        if area_VH[1][1] == "V":
            for lut in luts:
                if luts[lut].level == current_level:
                    if luts[lut].size[1] == int(num):
                        luts[lut].level += 1
                    this_luts[lut] = luts[lut]

                elif luts[lut].level > current_level:
                    if lut_level_up == 0:
                        luts[lut].level += 1
                    this_luts[lut] = luts[lut]

                elif luts[lut].level < current_level: 
                    past_luts[lut] = luts[lut]
        else:
            sys.exit()
            #for lut in luts:
             #   if luts[lut].level == current_level:
              #      if luts[lut].size[0] == int(num):
               #         luts[lut].level += 1
                #        this_luts[lut] = luts[lut]
                 #   else: this_luts[lut] = luts[lut]

                #elif luts[lut].level > current_level:
                 #   if lut_level_up == 0:
                  #      luts[lut].level += 1
                   # this_luts[lut] = luts[lut]

                #elif luts[lut].level < current_level: 
                 #   past_luts[lut] = luts[lut]

        for idx, lut in enumerate(this_luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        for idx, lut in enumerate(past_luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        

        final_luts = sorted(this_luts.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
        return final_luts
    
    """
    elif isinstance(area_VH[1], dict):
        v_results = []
        this_level_luts = {}
        this_luts = {}
        idx = 0

        for k, v in area_VH[1].items(): ##LEVEL UP logic when only one V remains
            if k.startswith('V') and v != 0:
                V_num = int(k[1:]) # 'V3' → 3
                max_v = v
            if k.startswith('H') and v != 0:
                H_num = int(k[1:])

        result = max_v / V_num
        #max_luts_num = max_luts_in_one_V(max_v,V_num,H_num,crossbar)
        if result % 2 == 0:
            result = result / 2
            v_results.append(int(result))
            v_results.append(int(result))
        else:
            result = result // 2
            v_results.append(int(result))
            v_results.append(int(result+1))


        one_V_luts = {}
        one_V_luts_sorted = {}
        for lut in luts:
            if luts[lut].level == current_level:
                one_V_luts[lut] = luts[lut]
            elif luts[lut].level > current_level:
                luts[lut].level += 1
                this_level_luts[lut] = luts[lut]

        #one_V_luts_sorted = dict(sorted(one_V_luts.items(), key=lambda item: item[1].fanlevel))
        from collections import Counter

        # 1. count the occurrences of each net (input)
        fanin_element_counts = Counter(
            net
            for obj in one_V_luts.values()
            for net in obj.fanin
        )

        # 2. LUT score: sum of the occurrence counts of all its input nets
        def lut_score(obj):
            return sum(fanin_element_counts[net] for net in obj.fanin)

        # 3. sort by score, descending (ties: more fanins first)
        one_V_luts_sorted = dict(
            sorted(
                one_V_luts.items(),
                key=lambda kv: (lut_score(kv[1]), len(kv[1].fanin)),
                reverse=True
            )
        )



        for lut in one_V_luts_sorted:
            if idx == v_results[0]:
                one_V_luts_sorted[lut].level += 1
                this_level_luts[lut] = one_V_luts_sorted[lut]
            else: 
                this_level_luts[lut] = one_V_luts_sorted[lut]
                idx +=1

        for idx, lut in enumerate(luts):
            for chek_lut in luts:
                if lut in luts[chek_lut].fanin:                
                    luts[lut].fanlevel = max(luts[lut].fanlevel, luts[chek_lut].level)

        merged = {**this_level_luts, **this_luts}
        final_luts = sorted(merged.values(), key=lambda x: (x.level, x.size[0], x.size[1]))
        return final_luts
    """


@timer
def update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up):
    global lut_max_V

    if lut_level_up == 1:
        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
    else: insert_info = mapping_info[current_level]
    
    num = int(area_VH[1][0])
    if area_VH[1][1] == "V" and int(num) > lut_max_V :return False

    if edit == 1:
        val = ()
        if current_level!=1:
            for i in range(4):
                if mapping_info[current_level-1][int(num)-2][i] !=0:
                    value = int(mapping_info[current_level-1][int(num)-2][i])
                    if value % 2 == 0:
                        val = (int(value/2),int(value/2))
                        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
                        mapping_info[current_level-1][int(num)-2][i] = val[0]
                        mapping_info.insert(current_level,insert_info)
                        mapping_info[current_level][int(num)-2][i] = val[1]
                        return mapping_info
                    else:
                        val = (int(value/2),int(value/2 +1))
                        insert_info = [[0 for _ in range(4)] for n in range(lut_max_V)]
                        mapping_info[current_level-1][int(num)-2][i] = val[0]
                        mapping_info.insert(current_level,insert_info)
                        mapping_info[current_level][int(num)-2][i] = val[1]
                        return mapping_info
            

            if lut_level_up == 1: mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
            mapping_info.insert(current_level,insert_info)
        else:
            insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]
            mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
            if lut_level_up == 1: mapping_info.insert(current_level,insert_info)

    if area_VH[1][1] == "V":
        insert_info[int(num)-2] = [_a + _b for _a, _b in zip(insert_info[int(num)-2], mapping_info[current_level-1][int(num)-2])]
        mapping_info[current_level-1][int(num)-2] = [0 for _ in range(len(mapping_info[current_level-1][int(num)-2]))]
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)
    else: #H
        for i in range(lut_max_V):
            insert_info[i][int(num)-2] += mapping_info[current_level-1][i][int(num)-2]
            mapping_info[current_level-1][i][int(num)-2] = 0
        if lut_level_up == 1: mapping_info.insert(current_level,insert_info)



    return mapping_info

########################
@timer
def map_to_crossbar(luts, crossbar, mapping_info):
        global Copy_IR_Cycle
        global Reset_Cycle
        global VNOR_Cycle
        global HNOR_Cycle
        global A_star_Cycle
        global Place_Cycle


        ordered_luts = sorted(luts.values(), key=lambda x: (x.level, x.size[0], x.size[1], len(x.fanin))) ########
        
        @timer
        def reset_crossbar_except_last_col(target_level):   #target_level : level of the lut to map - 1 
            reset_c=[]
            reset_IR_list = []
            global Attempt
            global Cycle
            global Path
            global Copy_IR_Cycle
            global Reset_Cycle

            for r in range(len(crossbar.grid)):
                for c in range(len(crossbar.grid[0])-1, -1, -1):
                    if not crossbar.grid[r][c]: cell_content=None
                    elif crossbar.grid[r][c] and '_' in crossbar.grid[r][c]:      #mapped lut
                        cell_content = crossbar.grid[r][c].split('_')[0]
                        if cell_content is not None and cell_content[0] =='~': 
                            cell_content = cell_content[1:]
                    elif crossbar.grid[r][c] and '_' not in crossbar.grid[r][c]:  #reset cells used by A* routes
                        crossbar.grid[r][c] = None
                        cell_content = None
                        if c not in reset_c:
                            reset_c.append(c)                                                                             ###define cell_content. A* reset
                    
                    if cell_content and '_' in crossbar.grid[r][c] :
                        if luts[cell_content].fanlevel < target_level: #no need to keep
                            cell_content2 = crossbar.grid[r][c].split('_')[1] 
                            if cell_content2 != "0" and cell_content2[0] == "~": cell_content2 = cell_content2[1:]
                            if cell_content2 == cell_content:
                                if c > int(len(crossbar.grid[0])*0.75):
                                    if (r,c) not in reset_IR_list:
                                        reset_IR_list.append((r,c)) # stacked IRs are handled separately
                                elif c <= luts[cell_content].col[-1]: 
                                    crossbar.grid[r][c] = None
                                    if c not in reset_c:
                                        reset_c.append(c)  
                
                            else:
                                crossbar.grid[r][c] = None #reset the mapped lut
                                if c not in reset_c:
                                    reset_c.append(c)  
                        

                        else: # move IR
                            if c < int(len(crossbar.grid[0])*0.75):
                                if c == luts[cell_content].col[-1]: ###########last_c
                                    if crossbar.grid[r][c].split('_')[1]!='0' :  
                                        for _c in range(len(crossbar.grid[0])-1, -1, -1):   #search for a cell to move the IR to, from the last col down
                                                if crossbar.grid[r][_c] is None:
                                                    temp = crossbar.grid[r][c] #keep the IR value in a temp variable
                                                    if c<_c:
                                                        if _c > int(len(crossbar.grid[0])*0.75):##  restrict where intermediate values are placed
                                                            crossbar.grid[r][_c] = temp #move the temp value to the end column
                                                            if crossbar.grid[r][c].split('_')[1][0]=='~':
                                                                crossbar.grid[r][_c] = crossbar.grid[r][_c].replace("_~","_")

                                                            else:
                                                                crossbar.grid[r][_c] = crossbar.grid[r][_c].replace("_","_~")

                                                            luts[cell_content].col.append(_c) #add so that this column is not reset
                                                            temp = temp.split('_')[0]
                                                            file_path = os.path.join(Path, "instructions.txt")

                                                            # open the file and write the data
                                                            with open(file_path, 'a') as f:
                                                                # format the line with an f-string
                                                                data = f"C{Cycle} Copy IR:{str(temp)} ({r}, {c}) to ({r}, {_c}) \n"
                                                                f.write(data)
                                                                Cycle += 1  # increment Cycle
                                                                Copy_IR_Cycle += 1

                                                            crossbar.grid[r][c] = None
                                                            if c not in reset_c:
                                                                reset_c.append(c)    
                                                            break

                                                        else:
                                                            done = False
                                                            for c_des in range(len(crossbar.grid[0])-1, -1, -1):
                                                                if done==True: break
                                                                for r_des in range(len(crossbar.grid)):
                                                                    if crossbar.grid[r_des][c_des] == None:
                                                                        temp2 = crossbar.grid[r][c_des]
                                                                        if temp2 == None:
                                                                            if temp.split("_")[1][0] == "~":
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                            else: 
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_","_~")
                                                                            #crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                            done=True
                                                                            break
                                                                        else: #IR already
                                                                            if temp2.split("_")[1][0] == "~":
                                                                                crossbar.grid[r_des][c_des] = crossbar.grid[r][c_des].replace("_~","_")
                                                                            else: 
                                                                                crossbar.grid[r_des][c_des] = crossbar.grid[r][c_des].replace("_","_~")

                                                                            if (r, c_des) in reset_IR_list:
                                                                                reset_IR_list.remove((r, c_des))
                                                                                reset_IR_list.append((r_des, c_des))

                                                                            crossbar.grid[r][c_des] = None

                                                                            if crossbar.grid[r][c].split('_')[1][0]=='~':
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_~","_")
                                                                                done=True
                                                                                break
                                                                            else:
                                                                                crossbar.grid[r][c_des] = crossbar.grid[r][c].replace("_","_~")
                                                                                done=True
                                                                                break

                                                            luts[cell_content].col.append(c_des+1) #add so that this column is not reset
                                                            file_path = os.path.join(Path, "instructions.txt")

                                                            if temp2 == None:
                                                                with open(file_path, 'a') as f:
                                                                # format the line with an f-string
                                                                    data = f"C{Cycle} Copy IR:{temp} ({r}, {c}) to ({r}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1  # increment Cycle
                                                                    Copy_IR_Cycle += 1
                                                            else:
                                                                temp2 = temp2.split("_")[0]
                                                                temp = temp.split("_")[0]
                                                                # open the file and write the data
                                                                with open(file_path, 'a') as f:
                                                                    # format the line with an f-string
                                                                    data = f"C{Cycle} Copy IR:{temp2} ({r}, {c_des+1}) to ({r_des}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1  # increment Cycle
                                                                    Copy_IR_Cycle += 1

                                                                    data = f"C{Cycle} Reset IR: ({r}, {c_des+1})\n"
                                                                    f.write(data)
                                                                    Cycle += 1
                                                                    Reset_Cycle += 1

                                                                    data = f"C{Cycle} Copy IR:{temp} ({r}, {c}) to ({r}, {c_des+1}) \n"
                                                                    f.write(data)
                                                                    Cycle += 1 
                                                                    Copy_IR_Cycle += 1

                                                            crossbar.grid[r][c] = None
                                                            if c not in reset_c:
                                                                reset_c.append(c)    
                                                            break

                                                        
                                                            
                                    else:
                                        crossbar.grid[r][c] = None
                                        if c not in reset_c:
                                            reset_c.append(c)    


                                elif c != luts[cell_content].col[-1]: 
                                    crossbar.grid[r][c] = None
                                    if c not in reset_c:
                                       reset_c.append(c)

            
            #for (r,c) in reset_IR_list:
            #filtered_list = [(fr, fc) for (r, c) in reset_IR_list if c <= int(len(crossbar.grid[0]) * 0.75)]
            from collections import defaultdict
            # group by c
            group_by_c = {}
            group_by_c = defaultdict(list)
            for r, c in reset_IR_list:
                group_by_c[c].append((r, c))

            reset_list = []
            reset_group_c_str = ""
            for key, source in group_by_c.items():
                if len(source) > 3:
                    for (gcr,gcc) in source: 
                        crossbar.grid[gcr][gcc] = None
                        reset_list.append((gcr,gcc))
                    reset_group_c_str = ', '.join(map(str, reset_list))
                    file_path = os.path.join(Path, "instructions.txt")
                    with open(file_path, 'a') as f:
                        data = f"C{Cycle} Reset IR (c={key}): {reset_group_c_str}\n"
                        f.write(data)
                        Cycle += 1
                        Reset_Cycle += 1
                        reset_group_c_str = ""
                        reset_list = []

            # group by r
            #group_by_r = defaultdict(list)
            #for r, c in reset_IR_list:
                #group_by_r[r].append((r, c))        


            reset_c = sorted(reset_c)
            reset_c_str = ', '.join(map(str, reset_c))
            
            file_path = os.path.join(Path, "instructions.txt")

            with open(file_path, 'a') as f:
                data = f"C{Cycle} Reset Column: {reset_c_str}\n"
                f.write(data)
                Cycle += 1
                Reset_Cycle += 1
                        

        @timer
        def map_lut_with_reset(lut, luts, area_VH):      #copy (A*), then reset
            print(f"Trying to map LUT at level {lut.level} with fanin {lut.fanin} and expression {lut.expr}")
            print(f"Trying to use area: {area_VH}")
            print(f"Product terms: {lut.product_terms}")
            print(f"Number of terms: {lut.product_terms}")

            if not crossbar.map_lut(lut, luts, area_VH):
                execute_Copy(luts, crossbar) 
                execute_NOR(lut.level, luts, crossbar)  # run the NORs of the LUTs placed so far before the reset
                current_level_luts.clear()
                reset_crossbar_except_last_col(lut.level-1)
                if not crossbar.map_lut(lut, luts, area_VH):
                    print(f"[FATAL] LUT {lut.name} does not fit even after the reset")
                    sys.exit()
                return True
            return True
        # the state before/after the reset can also be printed inside

        global current_level_luts
        current_level_luts = []
        i = 0
        modified = False
        done = False
        current_level = None
        level_up_list = []
        lut_level_up = 0
        level_up = False
        used_luts = []
        while done == False:
            if done == True: break
            else:
                if modified == True: 
                    modified = False
                else:
                    for lut in ordered_luts:
                        edit =0
                        if current_level is None:
                            current_level = lut.level
                            this_mapping_info = mapping_info[0]
                            area_VH = Blocking(this_mapping_info, crossbar)

                        while True:
                            if (None in area_VH and current_level ==1):
                                if None not in area_VH:
                                    past_area_VH = area_VH
                                area_VH = Blocking(this_mapping_info, crossbar)
                                print(area_VH)
                                if None in area_VH:
                                    ##if lut_level_up == 0: original_luts(luts, current_level,lut_level_up)
                                    ordered_luts = levelup_luts(luts, current_level,area_VH,lut_level_up,crossbar,ordered_luts) 
                                    lut_level_up += 1
                                    edit = 0
                                    if isinstance(area_VH[1], dict):
                                        for k, v in area_VH[1].items():
                                            if v != 0:
                                                if k.startswith('V'):
                                                    num = int(k[1:])  # 'V3' → 3
                                                    area_VH = (area_VH[0], [num, "V"])
                                                    edit = 1
                                                    break
                                    mapping_info = update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up)
                                    modified = True
                                else: break
                            else: 
                                lut_level_up = 0
                                break
                        
                        if modified == True: break

                        if lut.level != current_level:
                        #or (lut.level == current_level and level_up == True):
                            if modified == True: break
                            #if level_up == False: 
                            current_level = lut.level
                            if None not in area_VH:
                                past_area_VH = area_VH
                            print(f"\nFinished mapping level: {current_level-1}\n")
                            #  input("Press Enter to continue...")  
                            pass  # crossbar print omitted to keep the log small
                            print('\n'+'\n')
                            crossbar = execute_Copy(luts, crossbar)
                            pass  # crossbar print omitted to keep the log small
                            print('\n'+'\n')
                            execute_NOR(current_level -1, luts, crossbar)
                            current_level_luts = []
                            reset_crossbar_except_last_col(lut.level-1)
                            pass  # crossbar print omitted to keep the log small
                            print('\n'+'\n')      
                            print_runtime_summary()
                            print_section_summary()
                            #current_level += 1
                            while True: #next_level_area 
                                this_mapping_info = mapping_info[current_level-1]
                                area_VH = Blocking(this_mapping_info, crossbar)
                                print(area_VH)
                                if None in area_VH:
                                    ordered_luts = levelup_luts(luts,current_level,area_VH,lut_level_up,crossbar,ordered_luts)
                                    lut_level_up += 1
                                    level_up_list.append(current_level-1)
                                    edit = 0
                                    if isinstance(area_VH[1], dict):
                                        for k, v in area_VH[1].items():
                                            if v != 0:
                                                if k.startswith('V'):
                                                    num = int(k[1:])  # 'V3' → 3
                                                    area_VH = (area_VH[0], [num, "V"])
                                                    edit = 1
                                                    break
                    
                                    mapping_info = update_mapping_info(mapping_info, current_level, area_VH, edit, lut_level_up)
                                    modified = True
                                    level_up = True
                                else: 
                                    break
                        ##if modified == True: break

                ## handling for a single count
                
                            if modified == True: break  
                            level_up = False

                        if not map_lut_with_reset(lut, luts, area_VH): ##the LUT is placed here
                            print("Mapping failed even after resetting!")
                            break   

                        used_luts.append(lut)

                        if lut.level == current_level:
                            if lut not in current_level_luts:
                                current_level_luts.append(lut)
                     
                        if lut == ordered_luts[-1]:
                            current_level=lut.level
                            print("\nFinished mapping the last level\n")
                            crossbar = execute_Copy(luts, crossbar)
                            pass  # crossbar print omitted to keep the log small
                            execute_NOR(current_level - 1, luts, crossbar)
                            print(level_up_list)  

                            
                            file_path = os.path.join(Path, "instructions.txt")
                            with open(file_path, 'a') as f:
                                data = f"Place Cycle {Place_Cycle} / A_star_Cycle {A_star_Cycle} / Copy_IR_Cycle {Copy_IR_Cycle} / HNOR_Cycle {HNOR_Cycle} / VNOR_Cycle {VNOR_Cycle} / Reset_Cycle {Reset_Cycle} \n"
                                f.write(data)

                            if len(used_luts) == len(luts):
                                print ('all luts mapping successfully')
                            else:
                                print ("mapping fail")


                            done = True
                            #reverse the final outputs 

                            print_runtime_summary()
                            print_section_summary()
                    

                        if current_level == ordered_luts[0].level and ordered_luts[1].level == ordered_luts[0].level+1: 
                            del ordered_luts[0]
                            break   
                        


        temp_level_luts = ''.join(str(ordered_luts)+'\n')
        print('\n' + '\n' + '\n')
        print('-'*32)
        print("@@@@@@@@LUTS sorted by level")
        
        print(temp_level_luts)
    
        return crossbar


def print_runtime_summary():
    print("\n[ Function runtime summary ]")
    total_all = sum(data["total_time"] for data in runtimes.values())
    print(f"Total cumulative runtime: {total_all:.6f} s\n")
    for name, data in runtimes.items():
        count = data["count"]
        total = data["total_time"]
        avg = total / count if count else 0.0
        print(f"{name} calls {count} | total {total:.6f} s | avg {avg:.9f} s")

def print_section_summary():
    print("\n[ Section runtime summary ]")
    for name, times in section_times.items():
        count = len(times)
        total = sum(times)
        avg = total / count if count else 0.0
        print(f"{name} | calls {count} | total {total:.6f} s | avg {avg:.9f} s")




def plot_crossbar(crossbar):
    from igraph import Graph, plot

    rows = len(crossbar.grid)
    cols = len(crossbar.grid[0])

    g = Graph()

    # Add vertices
    for r in range(rows):
        for c in range(cols):
            g.add_vertex(name=str(crossbar.grid[r][c]) if crossbar.grid[r][c] is not None else f"{r},{c}")

    # Add edges
    for r in range(rows):
        for c in range(cols): 
            if c < cols - 1:
                g.add_edge(r*cols + c, r*cols + c + 1)  # horizontal
            if r < rows - 1:
                g.add_edge(r*cols + c, (r+1)*cols + c)  # vertical

    layout = g.layout_grid()
    visual_style = {}
    visual_style["bbox"] = (300, 300)
    visual_style["margin"] = 20
    visual_style["vertex_size"] = 20
    visual_style["vertex_label"] = [v["name"] for v in g.vs]
    

    plot(g, layout=layout, target="my_output.png", **visual_style)


verilog_input = open(_args.input, encoding="utf-8").read()
 
verilog_text1 = parse_verilog(verilog_input)
verilog_text2 = update_lut_info(verilog_text1)
print(verilog_text2)  
mapping_info = Optimizing_luts(verilog_text2)
print("-"*444)
#print(mapping_info)
crossbar_instance = initialize_crossbar(_cb_rows, _cb_cols)
verilog_crossbar3 = map_to_crossbar(verilog_text2, crossbar_instance, mapping_info)