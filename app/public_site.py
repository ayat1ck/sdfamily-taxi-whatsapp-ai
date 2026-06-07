from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings

router = APIRouter(tags=["public-site"])


def _layout(title: str, body: str) -> HTMLResponse:
    settings = get_settings()
    brand = settings.public_site_brand_name
    app_host = settings.app_host.rstrip("/")
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | {brand}</title>
  <meta name="description" content="{settings.public_site_description}">
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f7fb;
      --surface: #ffffff;
      --text: #101828;
      --muted: #475467;
      --border: #d0d5dd;
      --accent: #1570ef;
      --accent-soft: #eff6ff;
      --success: #067647;
      --max: 1100px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, Arial, sans-serif;
      color: var(--text);
      background: linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
      line-height: 1.5;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .wrap {{ max-width: var(--max); margin: 0 auto; padding: 0 24px; }}
    header {{
      border-bottom: 1px solid var(--border);
      background: rgba(255,255,255,0.92);
      backdrop-filter: blur(10px);
      position: sticky;
      top: 0;
    }}
    nav {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      min-height: 72px;
    }}
    .brand {{
      font-weight: 700;
      font-size: 20px;
      color: var(--text);
    }}
    .nav-links {{
      display: flex;
      gap: 18px;
      flex-wrap: wrap;
      font-size: 14px;
    }}
    main {{ padding: 48px 0 72px; }}
    .hero {{
      display: grid;
      grid-template-columns: 1.4fr 1fr;
      gap: 28px;
      align-items: stretch;
      margin-bottom: 28px;
    }}
    .panel {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 28px;
      box-shadow: 0 8px 24px rgba(16, 24, 40, 0.06);
    }}
    h1 {{
      margin: 0 0 14px;
      font-size: 42px;
      line-height: 1.1;
    }}
    h2 {{
      margin: 0 0 14px;
      font-size: 26px;
      line-height: 1.2;
    }}
    h3 {{
      margin: 0 0 10px;
      font-size: 18px;
    }}
    p {{
      margin: 0 0 16px;
      color: var(--muted);
    }}
    .lead {{
      font-size: 18px;
      max-width: 56ch;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 20px;
    }}
    .list {{
      margin: 0;
      padding-left: 20px;
      color: var(--muted);
    }}
    .meta-box {{
      display: grid;
      gap: 12px;
    }}
    .meta-row {{
      padding: 12px 14px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fcfcfd;
    }}
    .meta-label {{
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 4px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .status {{
      color: var(--success);
      font-weight: 600;
    }}
    footer {{
      border-top: 1px solid var(--border);
      padding: 24px 0 48px;
      background: rgba(255,255,255,0.78);
    }}
    .small {{
      color: var(--muted);
      font-size: 14px;
    }}
    @media (max-width: 900px) {{
      .hero, .grid {{
        grid-template-columns: 1fr;
      }}
      h1 {{ font-size: 34px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="wrap">
      <nav>
        <a class="brand" href="/">{brand}</a>
        <div class="nav-links">
          <a href="/">Overview</a>
          <a href="/contacts">Contacts</a>
          <a href="/privacy-policy">Privacy Policy</a>
          <a href="/terms-of-service">Terms of Service</a>
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
    <div class="wrap small">
      <div>{settings.public_site_legal_name}</div>
      <div>{settings.public_site_support_email} · {settings.public_site_support_phone}</div>
      <div>{settings.public_site_address}</div>
      <div>Service URL: <a href="{app_host}">{app_host}</a></div>
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
      <div class="panel">
        <div class="badge">WhatsApp onboarding workflow</div>
        <h1>{settings.public_site_brand_name}</h1>
        <p class="lead">{settings.public_site_description}</p>
        <p>
          {settings.public_site_brand_name} provides WhatsApp-based driver onboarding,
          document collection, registration tracking, and operator support for taxi fleet workflows.
        </p>
        <p>
          The service is used to collect driver data, receive required documents,
          validate applications, and support onboarding communication with drivers.
        </p>
      </div>
      <div class="panel meta-box">
        <div class="meta-row">
          <span class="meta-label">Legal entity</span>
          {settings.public_site_legal_name}
        </div>
        <div class="meta-row">
          <span class="meta-label">Support email</span>
          <a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a>
        </div>
        <div class="meta-row">
          <span class="meta-label">Support phone</span>
          <a href="tel:{settings.public_site_support_phone}">{settings.public_site_support_phone}</a>
        </div>
        <div class="meta-row">
          <span class="meta-label">Business address</span>
          {settings.public_site_address}
        </div>
        <div class="meta-row">
          <span class="meta-label">Service status</span>
          <span class="status">Operational</span>
        </div>
      </div>
    </section>
    <section class="grid">
      <div class="panel">
        <h3>Use case</h3>
        <p>
          Drivers communicate with the business through WhatsApp to complete onboarding and submit registration documents.
        </p>
      </div>
      <div class="panel">
        <h3>Data processed</h3>
        <p>
          Contact details, driver identity data, vehicle information, registration status, and supporting documents.
        </p>
      </div>
      <div class="panel">
        <h3>Support and compliance</h3>
        <p>
          Contact information, privacy details, and terms are published on this site for business verification and customer support.
        </p>
      </div>
    </section>
    """
    return _layout(settings.public_site_brand_name, body)


@router.get("/contacts", response_class=HTMLResponse)
def contacts() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel">
      <h1>Contact Information</h1>
      <p>This page provides the official public contact details for {settings.public_site_brand_name}.</p>
      <div class="meta-box">
        <div class="meta-row"><span class="meta-label">Business name</span>{settings.public_site_brand_name}</div>
        <div class="meta-row"><span class="meta-label">Legal name</span>{settings.public_site_legal_name}</div>
        <div class="meta-row"><span class="meta-label">Email</span><a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a></div>
        <div class="meta-row"><span class="meta-label">Phone</span><a href="tel:{settings.public_site_support_phone}">{settings.public_site_support_phone}</a></div>
        <div class="meta-row"><span class="meta-label">Address</span>{settings.public_site_address}</div>
      </div>
    </section>
    """
    return _layout("Contact Information", body)


@router.get("/privacy-policy", response_class=HTMLResponse)
def privacy_policy() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel">
      <h1>Privacy Policy</h1>
      <p>
        {settings.public_site_brand_name} collects and processes personal data submitted through WhatsApp
        and related onboarding workflows in order to review driver applications, verify identity and vehicle data,
        communicate with applicants, and manage registration with connected business systems.
      </p>
      <h2>Data we collect</h2>
      <ul class="list">
        <li>Phone number and communication history</li>
        <li>Full name, identification number, address, and date-related onboarding details</li>
        <li>Driver license details and vehicle information</li>
        <li>Uploaded documents and registration-related files</li>
      </ul>
      <h2>How we use data</h2>
      <ul class="list">
        <li>To process onboarding applications and communicate with drivers</li>
        <li>To maintain internal records and application status</li>
        <li>To transfer approved data to connected operational systems where required</li>
        <li>To provide support, auditability, and fraud prevention</li>
      </ul>
      <h2>Data sharing</h2>
      <p>
        Data may be shared with business tools used for onboarding, document storage,
        spreadsheet-based operations, and taxi fleet registration workflows where necessary for service delivery.
      </p>
      <h2>Contact</h2>
      <p>
        Privacy requests can be sent to <a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a>.
      </p>
    </section>
    """
    return _layout("Privacy Policy", body)


@router.get("/terms-of-service", response_class=HTMLResponse)
def terms_of_service() -> HTMLResponse:
    settings = get_settings()
    body = f"""
    <section class="panel">
      <h1>Terms of Service</h1>
      <p>
        {settings.public_site_brand_name} provides a business communication and onboarding service for taxi driver registration workflows.
      </p>
      <h2>Permitted use</h2>
      <ul class="list">
        <li>Users may communicate with the business through approved channels for onboarding and support purposes.</li>
        <li>Submitted information must be accurate and up to date.</li>
        <li>The service may be used to collect documents and operational details required for driver registration.</li>
      </ul>
      <h2>Business review</h2>
      <p>
        Submission of information does not guarantee approval, registration, or acceptance into a fleet workflow.
        Applications may be reviewed manually and may be declined if information is incomplete, inconsistent, or ineligible.
      </p>
      <h2>Service changes</h2>
      <p>
        The business may update workflows, document requirements, communication procedures, or operational integrations at any time.
      </p>
      <h2>Contact</h2>
      <p>
        Questions about these terms can be sent to <a href="mailto:{settings.public_site_support_email}">{settings.public_site_support_email}</a>.
      </p>
    </section>
    """
    return _layout("Terms of Service", body)
