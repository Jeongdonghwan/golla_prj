"""서버 파이썬 3.10 에서 깨지는 f-string 찾기(로컬 3.12 에서는 통과해 버리는 것).

    python scripts/py310_check.py $(git ls-files "*.py")

잡는 것: 따옴표 한 개짜리 f-string 의 {} 안에 같은 따옴표 문자열·역슬래시가 있는 경우, 같은 따옴표 f-string 겹침.
따옴표 세 개짜리(f\"\"\"...\"\"\") 안의 따옴표 한 개는 3.10 에서도 된다 — 잡지 않는다.
"""
import sys
import tokenize

bad = 0
for f in sys.argv[1:]:
    stack = []  # (따옴표, 세 개짜리인지)
    with open(f, "rb") as fh:
        for t in tokenize.tokenize(fh.readline):
            name = tokenize.tok_name[t.type]
            if name == "FSTRING_START":
                body = t.string.lstrip("rRfFbB")
                q, triple = body[0], body[:3] in ('"""', "'''")
                if stack and not stack[-1][1] and stack[-1][0] == q:
                    bad += 1
                    print(f"{f}:{t.start[0]} 같은 따옴표 f-string 겹침")
                stack.append((q, triple))
            elif name == "FSTRING_END":
                stack.pop()
            elif stack and not stack[-1][1] and name == "STRING":
                s = t.string.lstrip("rRbBuU")
                if s[0] == stack[-1][0] or "\\" in t.string:
                    bad += 1
                    print(f"{f}:{t.start[0]} {t.string[:50]}")
print("3.10 문법 문제", bad)
sys.exit(1 if bad else 0)
