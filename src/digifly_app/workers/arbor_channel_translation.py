"""Explicit Arbor-dialect edits for the locked Circuit Builder channels.

The native source stays immutable. Generated files and input hashes belong to the
run. Compilation and response comparisons, not this transformation, qualify a port.
"""
from __future__ import annotations
import re


def _block(text: str, opening: int) -> tuple[int, int]:
    start = text.index("{", opening)
    depth = 1
    end = start + 1
    while depth:
        if text[end] == "{":
            depth += 1
        elif text[end] == "}":
            depth -= 1
        end += 1
    return start, end


def translate(key: str, source: str) -> str:
    text = re.sub(r"COMMENT\b.*?ENDCOMMENT", "", source, flags=re.S)
    # Ions are intrinsic Arbor symbols; duplicate declarations are illegal.
    text = re.sub(r"^\s*(?:ena|ek|eca|ina|ik|ica|dt)\s+\([^\n]*\)\s*$", "", text, flags=re.M)
    # Preserve NEURON's clamped linear interpolation on the locked rate grid.
    # modcc has no TABLE/floor: bounded binary subtraction finds the index.
    if re.search(r"\bTABLE\b", text):
        match = re.search(r"PROCEDURE trates\(v \(mV\)\)\s*\{\s*TABLE ([\w, ]+)\s*\n\s*DEPEND[^\n]*\n\s*FROM vmin TO vmax WITH 1600\s*\n\s*rates\(v\)\s*\}", text)
        if not match:
            raise ValueError("Unsupported rate TABLE: expected the locked 1600-interval trates grid")
        names = [x.strip() for x in match[1].split(",")]
        local = ", ".join(["grid", "index", "remainder", "fraction", "table_v", *[f"low_{x}" for x in names]])
        body = [f"PROCEDURE trates(v (mV)) {{\n LOCAL {local}",
                "if (v <= vmin) {\n rates(vmin)\n } else {",
                "if (v >= vmax) {\n rates(vmax)\n } else {",
                "grid = (v-vmin)*1600/(vmax-vmin)", "index = 0", "remainder = grid"]
        for bit in (1024,512,256,128,64,32,16,8,4,2,1):
            body.append(f"if (remainder >= {bit}) {{\n index = index+{bit}\n remainder = remainder-{bit}\n }}")
        body += ["fraction = grid-index", "table_v = vmin+index*(vmax-vmin)/1600", "rates(table_v)"]
        body += [f"low_{x} = {x}" for x in names]
        body += ["table_v = vmin+(index+1)*(vmax-vmin)/1600", "rates(table_v)"]
        body += [f"{x} = low_{x}+fraction*({x}-low_{x})" for x in names]
        body += ["} } }"]
        rate_match = re.search(r"PROCEDURE rates\(vm \(mV\)\)\s*\{", text)
        if not rate_match:
            raise ValueError("Locked TABLE rate procedure was not found")
        start, end = _block(text, rate_match.start())
        equations = text[start+1:end-1]
        replacement = "\n".join(body)
        # Inline the pure equations: repeated calls that write ASSIGNED values
        # inside nested branches can be elided by modcc's procedure optimizer.
        replacement = re.sub(r"rates\((vmin|vmax|table_v)\)",
                             lambda m: re.sub(r"\bvm\b", m[1], equations), replacement)
        text = text[:match.start()] + replacement + text[match.end():]
    text = re.sub(r"\s*\(degC\)", "", text)
    if key in {"para", "para_alt"}:
        # Matrix-tree stationary weights of the C1-C2-O1-I1 cycle. O2 and
        # I2 are reversible leaves. This solves the same six-state stationary
        # linear system, rather than inventing a resting state or warm-up.
        replacements = {
            "a": "C1C2_a", "b": "C2C1_a", "c": "C2O1_a", "d": "O1C2_a",
            "e": "O1I1_a", "f": "I1O1_a", "g": "I1C1_a", "h": "C1I1_a",
        }
        polynomials = ["b*d*f+b*d*g+b*e*g+c*e*g", "a*d*f+a*d*g+a*e*g+d*f*h",
                       "a*c*f+a*c*g+b*f*h+c*f*h", "a*c*e+b*d*h+b*e*h+c*e*h"]
        formulas = [re.sub(r"\b[a-h]\b", lambda m: replacements[m[0]], p) for p in polynomials]
        initialization = "rates(v)\n" + "\n".join(f"w{i+1} = {p}" for i, p in enumerate(formulas))
        initialization += """
        w5 = w2*C2O2_a/O2C2_a
        w6 = w4*I1I2_a/I2I1_a
        total = w1+w2+w3+w4+w5+w6
        C1 = w1/total
        C2 = w2/total
        O1 = w3/total
        I1 = w4/total
        O2 = w5/total
        I2 = w6/total
        """
        text = re.sub(r"INITIAL\s*\{", "INITIAL {\nLOCAL w1, w2, w3, w4, w5, w6, total", text)
        text, count = re.subn(r"SOLVE kin\s+STEADYSTATE sparse", initialization, text)
        if count != 1:
            raise ValueError("Locked Markov initialization pattern was not found exactly once")
    # Strip argument units only in declarations, not mathematical parentheses.
    text = re.sub(r"\b(PROCEDURE|FUNCTION)\s+(\w+)\(((?:[^()]|\([^()]*\))*)\)",
                  lambda m: f"{m[1]} {m[2]}({re.sub(r'\([^()]*\)', '', m[3]).strip()})", text)
    parameters = []
    parameter_match = re.search(r"\bPARAMETER\s*\{", text)
    if parameter_match:
        start, end = _block(text, parameter_match.start())
        parameters = re.findall(r"^\s*([A-Za-z]\w*)", text[start + 1:end - 1], flags=re.M)
    # Arbor procedures must receive external parameters explicitly.
    additions = {}
    procedure_bodies = {}
    for match in re.finditer(r"\bPROCEDURE\s+(\w+)\(([^)]*)\)\s*\{", text):
        start, end = _block(text, match.start())
        body = text[start + 1:end - 1]
        procedure_bodies[match[1]] = body
        existing = set(re.findall(r"[A-Za-z]\w*", match[2]))
        used = set(re.findall(r"\b[A-Za-z]\w*\b", body))
        extra = [name for name in parameters if name in used and name not in existing]
        if extra:
            additions[match[1]] = extra
    # A procedure calling another must also receive its external parameters.
    while True:
        changed = False
        for name, body in procedure_bodies.items():
            required = set(additions.get(name, ()))
            for callee, extra in list(additions.items()):
                if re.search(rf"\b{re.escape(callee)}\(", body):
                    required.update(extra)
            ordered = [p for p in parameters if p in required]
            if ordered != additions.get(name, []):
                additions[name] = ordered
                changed = True
        if not changed:
            break
    for name, extra in additions.items():
        text = re.sub(rf"\b{re.escape(name)}\(([^)]*)\)",
                      lambda m: f"{name}({m[1]}, {', '.join(extra)})", text)
    return f"TITLE Digifly Arbor translation of {key}\n" + re.sub(r"^TITLE[^\n]*\n", "", text)
