from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings

router = APIRouter(tags=["public-site"])


def _layout(title: str, body: str) -> HTMLResponse:
    settings = get_settings()
    brand = settings.public_site_brand_name
    app_host = settings.app_host.rstrip("/")
    html = f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | {brand}</title>
  <meta name="description" content="{settings.public_site_description}">
  <style>
    :root {{
      --bg: #090909;
      --surface: #111111;
      --text: #f8fafc;
      --muted: #d0d5dd;
      --muted-2: #98a2b3;
      --line: rgba(255, 197, 15, 0.22);
      --accent: #ffc50f;
      --accent-2: #ffb800;
      --max: 1180px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ background: var(--bg); }}
    body {{
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(255, 197, 15, 0.18), transparent 28%),
        radial-gradient(circle at bottom right, rgba(255, 197, 15, 0.12), transparent 32%),
        linear-gradient(180deg, #0d0d0d 0%, #080808 100%);
      font-family: Inter, Arial, sans-serif;
      line-height: 1.5;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .wrap {{ max-width: var(--max); margin: 0 auto; padding: 0 24px; }}
    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      background: rgba(9, 9, 9, 0.92);
      backdrop-filter: blur(14px);
    }}
    nav {{
      min-height: 76px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 12px;
      color: var(--text);
      font-weight: 800;
      letter-spacing: 0.02em;
      font-size: 21px;
    }}
    .brand-badge {{
      width: 46px;
      height: 46px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(180deg, var(--accent) 0%, var(--accent-2) 100%);
      color: #111;
      font-weight: 900;
      box-shadow: 0 10px 24px rgba(255, 197, 15, 0.28);
    }}
    .nav-links {{
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
      font-size: 14px;
    }}
    .nav-links a {{ color: #f2f4f7; }}
    main {{ padding: 36px 0 72px; }}
    .hero {{
      display: grid;
      grid-template-columns: 1.3fr 0.9fr;
      gap: 22px;
      margin-bottom: 22px;
    }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(180deg, rgba(255,255,255,0.02) 0%, rgba(255,255,255,0.01) 100%);
      box-shadow: 0 18px 40px rgba(0,0,0,0.32);
    }}
    .hero-main {{
      padding: 34px;
      position: relative;
      overflow: hidden;
      min-height: 510px;
      background:
        linear-gradient(130deg, rgba(255, 197, 15, 0.13) 0%, rgba(255, 197, 15, 0.03) 28%, transparent 58%),
        linear-gradient(180deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%);
    }}
    .hero-main:before {{
      content: "";
      position: absolute;
      inset: auto -90px -110px auto;
      width: 380px;
      height: 380px;
      border-radius: 50%;
      background: radial-gradient(circle, rgba(255, 197, 15, 0.28) 0%, rgba(255, 197, 15, 0.03) 54%, transparent 70%);
      pointer-events: none;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 11px;
      border: 1px solid rgba(255, 197, 15, 0.32);
      border-radius: 999px;
      background: rgba(255, 197, 15, 0.08);
      color: var(--accent);
      font-weight: 700;
      font-size: 13px;
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: 60px;
      line-height: 0.96;
      letter-spacing: 0;
      text-transform: uppercase;
      max-width: 9ch;
    }}
    .accent {{ color: var(--accent); }}
    .lead {{
      margin: 0 0 20px;
      max-width: 56ch;
      color: var(--muted);
      font-size: 17px;
    }}
    .offer {{
      display: inline-flex;
      flex-direction: column;
      gap: 2px;
      padding: 16px 18px;
      border-radius: 8px;
      background: linear-gradient(180deg, var(--accent) 0%, var(--accent-2) 100%);
      color: #111;
      font-weight: 900;
      text-transform: uppercase;
      box-shadow: 0 18px 34px rgba(255, 197, 15, 0.22);
      margin: 10px 0 18px;
    }}
    .offer small {{
      font-size: 14px;
      font-weight: 800;
      opacity: 0.88;
    }}
    .offer strong {{
      font-size: 58px;
      line-height: 0.94;
    }}
    .offer span {{
      font-size: 20px;
    }}
    .tag-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 18px 0 26px;
    }}
    .tag {{
      padding: 10px 12px;
      border-radius: 8px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.08);
      color: #f2f4f7;
      font-size: 14px;
      min-height: 48px;
      display: inline-flex;
      align-items: center;
    }}
    .hero-summary {{
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }}
    .summary-item {{
      padding: 14px;
      border-radius: 8px;
      background: rgba(0,0,0,0.24);
      border: 1px solid rgba(255, 197, 15, 0.14);
    }}
    .summary-value {{
      display: block;
      color: var(--accent);
      font-size: 24px;
      font-weight: 800;
      margin-bottom: 4px;
    }}
    .summary-label {{
      color: var(--muted);
      font-size: 13px;
    }}
    .hero-side {{
      display: grid;
      gap: 16px;
    }}
    .info-card {{
      padding: 24px;
      background:
        linear-gradient(180deg, rgba(255, 197, 15, 0.08) 0%, rgba(255, 197, 15, 0.01) 100%),
        var(--surface);
    }}
    .info-card h2 {{
      margin: 0 0 12px;
      font-size: 24px;
      text-transform: uppercase;
    }}
    .info-card p {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .contact-list, .stack {{
      display: grid;
      gap: 10px;
    }}
    .info-row {{
      padding: 12px 14px;
      border-radius: 8px;
      background: rgba(255,255,255,0.03);
      border: 1px solid rgba(255,255,255,0.06);
    }}
    .k {{
      display: block;
      color: var(--muted-2);
      font-size: 12px;
      text-transform: uppercase;
      margin-bottom: 5px;
      letter-spacing: 0.04em;
    }}
    .v {{ color: #fff; }}
    .sections {{
      display: grid;
      gap: 22px;
    }}
    .grid-3 {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
    }}
    .feature {{
      padding: 24px;
      min-height: 198px;
    }}
    .feature h3 {{
      margin: 0 0 10px;
      color: var(--accent);
      font-size: 20px;
      text-transform: uppercase;
    }}
    .feature p {{
      margin: 0;
      color: var(--muted);
    }}
    .doc {{
      padding: 28px;
    }}
    .doc h1 {{
      max-width: none;
      font-size: 40px;
      line-height: 1.04;
      margin-bottom: 16px;
    }}
    .doc h2 {{
      margin: 22px 0 10px;
      font-size: 22px;
      color: var(--accent);
      text-transform: uppercase;
    }}
    .doc p {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .doc ul {{
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
    }}
    .doc li {{ margin-bottom: 8px; }}
    footer {{
      border-top: 1px solid rgba(255,255,255,0.08);
      background: rgba(0,0,0,0.28);
      padding: 24px 0 44px;
    }}
    .footer-grid {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: start;
    }}
    .small {{
      color: var(--muted-2);
      font-size: 14px;
    }}
    @media (max-width: 960px) {{
      .hero, .grid-3, .footer-grid {{
        grid-template-columns: 1fr;
      }}
      h1 {{ font-size: 46px; max-width: none; }}
      .hero-summary {{ grid-template-columns: 1fr; }}
      .hero-main {{ min-height: auto; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <nav>
        <a class="brand" href="/">
          <span class="brand-badge">SD</span>
          <span>{brand}</span>
        </a>
        <div class="nav-links">
          <a href="/">Главная</a>
          <a href="/contacts">Контакты</a>
          <a href="/privacy-policy">Privacy Policy</a>
          <a href="/terms-of-service">Terms</a>
        </div>
      </nav>
    </div>
  </header>
  <main>
    <div class="wrap">
      {body}
    </div>
  </main>
  <footer>
    <div class="wrap footer-grid small">
      <div>
        <div>{settings.public_site_legal_name}</div>
        <div>{settings.public_site_support_email} · {settings.public_site_support_phone}</div>
        <div>WhatsApp для регистрации: {settings.public_site_whatsapp_phone}</div>
        <div>{settings.public_site_address}</div>
      </div>
      <div>
        <div>ОКЭД: {settings.public_site_oked}</div>
        <div>Service URL: <a href="{app_host}">{app_host}</a></div>
      </div>
    </div>
  </footer>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="hero">
      <div class="panel hero-main">
        <div class="eyebrow">WhatsApp onboarding · Таксопарк</div>
        <h1>SD Family <span class="accent">Taxi</span></h1>
        <p class="lead">
          {settings.public_site_description} Сервис используется для привлечения водителей,
          сбора документов, регистрации заявок и сопровождения подключения к таксопарку.
        </p>
        <div class="offer">
          <small>Твой бонус</small>
          <strong>5000 ₸</strong>
          <span>за 50 заказов</span>
        </div>
        <div class="tag-row">
          <div class="tag">Моментальные выплаты</div>
          <div class="tag">Поддержка водителей 24/7</div>
          <div class="tag">Регистрация через WhatsApp</div>
          <div class="tag">Документы и анкета в одном потоке</div>
        </div>
        <div class="hero-summary">
          <div class="summary-item">
            <span class="summary-value">2%</span>
            <span class="summary-label">комиссия</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">24/7</span>
            <span class="summary-label">техподдержка</span>
          </div>
          <div class="summary-item">
            <span class="summary-value">KZ</span>
            <span class="summary-label">работа по Казахстану</span>
          </div>
        </div>
      </div>
      <div class="hero-side">
        <div class="panel info-card">
          <h2>Контакты</h2>
          <p>Публичные данные компании для связи, поддержки и верификации бизнес-аккаунта.</p>
          <div class="contact-list">
            <div class="info-row"><span class="k">Компания</span><span class="v">{settings.public_site_legal_name}</span></div>
            <div class="info-row"><span class="k">Email</span><span class="v"><a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a></span></div>
            <div class="info-row"><span class="k">Контактный телефон</span><span class="v"><a href="tel:{settings.public_site_support_phone}">{settings.public_site_support_phone}</a></span></div>
            <div class="info-row"><span class="k">WhatsApp для регистрации</span><span class="v"><a href="tel:{settings.public_site_whatsapp_phone}">{settings.public_site_whatsapp_phone}</a></span></div>
            <div class="info-row"><span class="k">Адрес</span><span class="v">{settings.public_site_address}</span></div>
            <div class="info-row"><span class="k">ОКЭД</span><span class="v">{settings.public_site_oked}</span></div>
          </div>
        </div>
        <div class="panel info-card">
          <h2>Как работает сервис</h2>
          <div class="stack">
            <div class="info-row"><span class="k">1</span><span class="v">Водитель пишет в WhatsApp и проходит анкету.</span></div>
            <div class="info-row"><span class="k">2</span><span class="v">Сервис собирает документы и данные водителя.</span></div>
            <div class="info-row"><span class="k">3</span><span class="v">Заявка сохраняется и передается в рабочие системы.</span></div>
          </div>
        </div>
      </div>
    </section>
    <section class="sections">
      <div class="grid-3">
        <div class="panel feature">
          <h3>Для водителей</h3>
          <p>
            Быстрый онбординг, прозрачная регистрация, моментальные выплаты и поддержка без необходимости приезжать в офис на каждый шаг.
          </p>
        </div>
        <div class="panel feature">
          <h3>Для менеджеров</h3>
          <p>
            Централизованный прием заявок, история переписки, документы, статусы регистрации и ручной контроль операционных действий.
          </p>
        </div>
        <div class="panel feature">
          <h3>Для Meta Verification</h3>
          <p>
            На сайте опубликованы контакты, правовая информация, политика конфиденциальности и описание того, как используется WhatsApp.
          </p>
        </div>
      </div>
    </section>
    """
    return _layout(settings.public_site_brand_name, body)


@router.get("/contacts", response_class=HTMLResponse)
def contacts() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel doc">
      <h1>Контактная информация</h1>
      <p>
        Официальные публичные данные компании {settings.public_site_brand_name}, используемые для связи,
        поддержки пользователей и верификации интеграций.
      </p>
      <div class="stack">
        <div class="info-row"><span class="k">Бренд</span><span class="v">{settings.public_site_brand_name}</span></div>
        <div class="info-row"><span class="k">Юридическое наименование</span><span class="v">{settings.public_site_legal_name}</span></div>
        <div class="info-row"><span class="k">Основной ОКЭД</span><span class="v">{settings.public_site_oked}</span></div>
        <div class="info-row"><span class="k">Email</span><span class="v"><a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a></span></div>
        <div class="info-row"><span class="k">Контактный телефон</span><span class="v"><a href="tel:{settings.public_site_support_phone}">{settings.public_site_support_phone}</a></span></div>
        <div class="info-row"><span class="k">WhatsApp номер бота</span><span class="v"><a href="tel:{settings.public_site_whatsapp_phone}">{settings.public_site_whatsapp_phone}</a></span></div>
        <div class="info-row"><span class="k">Адрес</span><span class="v">{settings.public_site_address}</span></div>
      </div>
    </section>
    """
    return _layout("Контакты", body)


@router.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel doc">
      <h1>Privacy Policy</h1>
      <p>
        {settings.public_site_brand_name} collects and processes personal data submitted through WhatsApp
        and related onboarding workflows to review driver applications, verify identity and vehicle data,
        communicate with applicants, and manage registration in connected operational systems.
      </p>
      <h2>What data we collect</h2>
      <ul>
        <li>Phone number and message history within the onboarding flow</li>
        <li>Driver full name, address, identity number, and application details</li>
        <li>Driver license and vehicle information</li>
        <li>Uploaded supporting documents and application status metadata</li>
      </ul>
      <h2>How data is used</h2>
      <ul>
        <li>To process driver onboarding and communicate with applicants</li>
        <li>To maintain operational records and registration history</li>
        <li>To support document verification and application review</li>
        <li>To transfer required data into connected fleet and storage systems where applicable</li>
      </ul>
      <h2>Contact for privacy requests</h2>
      <p>
        For privacy-related questions, contact <a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a>.
      </p>
    </section>
    """
    return _layout("Privacy Policy", body)


@router.get("/terms-of-service", response_class=HTMLResponse)
def terms_of_service() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel doc">
      <h1>Terms of Service</h1>
      <p>
        {settings.public_site_brand_name} provides a communication and onboarding workflow for taxi driver registration and support.
      </p>
      <h2>Use of service</h2>
      <ul>
        <li>Users may submit information and documents for onboarding and support purposes.</li>
        <li>All provided data must be accurate, current, and lawful to process.</li>
        <li>Use of the service does not guarantee approval or successful registration.</li>
      </ul>
      <h2>Operational review</h2>
      <p>
        Applications may be reviewed manually and may be declined if information is incomplete, inconsistent, duplicated, or ineligible.
      </p>
      <h2>Changes</h2>
      <p>
        The business may update its onboarding requirements, communication procedures, and connected systems at any time.
      </p>
      <h2>Support contact</h2>
      <p>
        For service questions, contact <a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a>.
      </p>
    </section>
    """
    return _layout("Terms of Service", body)
