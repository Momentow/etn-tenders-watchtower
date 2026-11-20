from watchtower.net import detect_block

TENDER_LIST = (
    "<html><head><title>Ausschreibungen</title></head><body>"
    "<form action='/login'><input name=user><input type=password name=pw><button>Anmelden</button></form>"
    + "".join(
        f"<a href='/t/{i}'>Ausschreibung {i}: Lieferung von Büromaterial für die Stadtverwaltung</a>" for i in range(40)
    )
    + "</body></html>"
)


def kinds(signals):
    return [(s.kind, s.strong) for s in signals]


def test_plain_page_has_no_signals():
    assert detect_block("<html><body><a href='/t/1'>Tender</a></body></html>") == []


def test_cloudflare_challenge_is_strong_captcha():
    html = (
        "<html><head><title>Just a moment...</title></head><body>"
        "<div class='cf-turnstile'></div><script src='https://challenges.cloudflare.com/x.js'></script></body></html>"
    )
    [signal] = detect_block(html, status=403)
    assert (signal.kind, signal.strong) == ("captcha", True)
    assert any("Turnstile" in e for e in signal.evidence)


def test_recaptcha_widget_on_a_content_page_is_weak():
    html = TENDER_LIST.replace("</body>", "<div class='g-recaptcha' data-sitekey='k'></div></body>")
    signals = detect_block(html, status=200)
    assert ("captcha", False) in kinds(signals)


def test_login_page_is_strong():
    html = "<html><body><h1>Please log in</h1><form><input name=u><input type=password></form></body></html>"
    [signal] = detect_block(html, url="https://portal.example/auth/login", status=200)
    assert (signal.kind, signal.strong) == ("login", True)
    assert any("login URL" in e for e in signal.evidence)


def test_login_box_next_to_tender_list_is_weak():
    assert kinds(detect_block(TENDER_LIST, url="https://portal.example/ausschreibungen")) == [("login", False)]


def test_http_401_is_strong_login():
    [signal] = detect_block("", status=401, headers={"WWW-Authenticate": "Basic realm=x"})
    assert (signal.kind, signal.strong) == ("login", True)


def test_login_url_alone_is_not_a_block():
    assert detect_block("<html><body>Welcome</body></html>", url="https://portal.example/login") == []


def test_captcha_input_field_detected():
    html = "<html><body><form><img src='/captcha.png'><input name='captcha_code'></form></body></html>"
    [signal] = detect_block(html, status=200)
    assert signal.kind == "captcha"
