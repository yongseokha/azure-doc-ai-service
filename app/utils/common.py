import re

_PERSONAL_INFORMATION_REGEX_LIST = [
    r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d)",  # 주민등록번호
    r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)",  # 휴대폰번호
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",  # 이메일
]


def is_personal_information(value: str = "") -> bool:
    personal_information_yn = False
    if not value or not value.strip():
        personal_information_yn = False
    else:
        for regex in _PERSONAL_INFORMATION_REGEX_LIST:
            if bool(re.search(regex, value)):
                personal_information_yn = True
    return personal_information_yn
