"""IAAA 统一身份认证 + 教学网（Blackboard）SSO 登录。"""

import getpass
import http.cookiejar
import os

import requests

from .util import (
    COOKIES_PATH,
    UA,
    ensure_dirs,
    load_config,
    rand_str,
    save_config,
)

IAAA_IS_MOBILE_AUTHEN = "https://iaaa.pku.edu.cn/iaaa/isMobileAuthen.do"
IAAA_OAUTH_LOGIN = "https://iaaa.pku.edu.cn/iaaa/oauthlogin.do"

OAUTH_REDIR = "http://course.pku.edu.cn/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin"
SSO_LOGIN = "https://course.pku.edu.cn/webapps/bb-sso-BBLEARN/execute/authValidate/campusLogin"
BB_HOME = "https://course.pku.edu.cn/webapps/portal/execute/tabs/tabAction"


class LoginError(Exception):
    pass


def new_session():
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def normalize_cookies(session):
    """把同名 cookie（Blackboard 会按 /webapps/xxx 路径下发多份 JSESSIONID）
    归一化成一份、路径统一为 /，避免重载后选到已失效的那份。"""
    best = {}
    for c in session.cookies:
        key = (c.domain, c.name)
        cur = best.get(key)
        if cur is None or len(c.path or "/") >= len(cur.path or "/"):
            best[key] = c
    session.cookies.clear()
    for c in best.values():
        session.cookies.set(
            c.name,
            c.value,
            domain=c.domain,
            path="/",
            secure=bool(c.secure),
        )


def save_session(session):
    ensure_dirs()
    normalize_cookies(session)
    jar = http.cookiejar.MozillaCookieJar(COOKIES_PATH)
    for c in session.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)
    try:
        os.chmod(COOKIES_PATH, 0o600)
    except OSError:
        pass


def load_session():
    """加载缓存会话；失败返回 None。"""
    ensure_dirs()
    if not os.path.exists(COOKIES_PATH):
        return None
    try:
        jar = http.cookiejar.MozillaCookieJar(COOKIES_PATH)
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        return None
    s = new_session()
    for c in jar:
        s.cookies.set_cookie(c)
    normalize_cookies(s)
    return s


def fetch_homepage(session):
    r = session.get(
        BB_HOME,
        params={"tab_tab_group_id": "_1_1"},
        timeout=30,
        headers={"Referer": "https://course.pku.edu.cn/"},
    )
    r.raise_for_status()
    if "/webapps/login/" in r.url:
        return None
    return r.text


def is_logged_in(session):
    try:
        return fetch_homepage(session) is not None
    except requests.RequestException:
        return False


def check_otp_required(session, username):
    """返回 True 表示需要手机令牌（OTP）。"""
    try:
        r = session.get(
            IAAA_IS_MOBILE_AUTHEN,
            params={"appId": "blackboard", "userName": username, "_rand": rand_str()},
            timeout=15,
        )
        data = r.json()
        return data.get("authenMode") == "OTP"
    except Exception:
        return False


def iaaa_oauth_login(session, username, password, otp_code=""):
    """向 IAAA 提交账号密码，返回 token。"""
    r = session.post(
        IAAA_OAUTH_LOGIN,
        data={
            "appid": "blackboard",
            "userName": username,
            "password": password,
            "randCode": "",
            "smsCode": "",
            "otpCode": otp_code,
            "redirUrl": OAUTH_REDIR,
        },
        timeout=30,
        headers={"Referer": "https://iaaa.pku.edu.cn/"},
    )
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        raise LoginError(f"IAAA 返回了非 JSON 响应（HTTP {r.status_code}），请稍后重试")

    if not data.get("success"):
        err = data.get("errors") or {}
        code = err.get("code", "?")
        msg = err.get("msg", "未知错误")
        hint = {
            "E01": "用户名或密码错误",
            "E05": "手机令牌验证码错误",
            "E21": "尝试次数过多，请半小时后再试",
        }.get(code, "")
        raise LoginError(f"登录失败[{code}] {hint or msg}")

    token = data.get("token")
    if not token:
        raise LoginError(f"登录响应缺少 token: {data}")
    return token


def bb_sso_login(session, token):
    """用 IAAA token 换取教学网会话。"""
    r = session.get(
        SSO_LOGIN,
        params={"_rand": rand_str(), "token": token},
        timeout=30,
        allow_redirects=True,
    )
    r.raise_for_status()
    if "/webapps/login/" in r.url:
        raise LoginError("教学网 SSO 登录失败（可能 token 已过期），请重试")


def login(session, username, password, otp_code=None, interactive=True):
    """完整登录流程：IAAA -> 教学网 SSO。"""
    if otp_code is None and interactive and check_otp_required(session, username):
        try:
            otp_code = input("该账号开启了手机令牌，请输入动态验证码: ").strip()
        except EOFError:
            raise LoginError("需要手机令牌验证码，请在交互式终端中登录")
    otp_code = otp_code or ""

    token = iaaa_oauth_login(session, username, password, otp_code)
    bb_sso_login(session, token)

    if not is_logged_in(session):
        raise LoginError("登录后校验失败，请重试")

    cfg = load_config()
    cfg["username"] = username
    save_config(cfg)
    save_session(session)


def ensure_login(session=None, interactive=True, force=False):
    """确保拿到可用会话；优先复用缓存，过期则提示登录。"""
    session = session or load_session() or new_session()
    if not force and is_logged_in(session):
        return session

    if not interactive and not force:
        raise LoginError("会话已过期，请先运行 `python3 -m pku_recording login` 登录")

    cfg = load_config()
    default_user = cfg.get("username", "")
    prompt = f"学号/工号[{default_user}]: " if default_user else "学号/工号: "
    try:
        username = input(prompt).strip() or default_user
        if not username:
            raise LoginError("未输入学号")
        password = getpass.getpass("密码（不会保存）: ")
    except EOFError:
        raise LoginError("当前不是交互式终端，请先运行 `pku-recording login` 完成登录")
    if not password:
        raise LoginError("未输入密码")

    login(session, username, password, interactive=True)
    print("登录成功 ✓")
    return session


def logout():
    if os.path.exists(COOKIES_PATH):
        os.remove(COOKIES_PATH)
