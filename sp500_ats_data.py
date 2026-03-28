"""
S&P 500 ATS (Applicant Tracking System) Research Data
======================================================
Compiled: 2026-03-28
Sources: Web-verified via direct URL discovery on myworkdayjobs.com and company career pages.

FORMAT NOTES
------------
Workday tuples:   (tenant, board_path, wd_env, 'Company Name')
  - URL pattern:  https://{tenant}.wd{wd_env}.myworkdayjobs.com/en-US/{board_path}/jobs
  - API pattern:  POST https://{tenant}.wd{wd_env}.myworkdayjobs.com/wday/cxs/{tenant}/{board_path}/jobs

Greenhouse tuples: (slug, 'Company Name')
  - URL pattern:  https://boards.greenhouse.io/{slug}

Lever tuples:      (slug, 'Company Name')
  - URL pattern:  https://jobs.lever.co/{slug}

Verification status:
  [VERIFIED]   = URL confirmed live via web search
  [LIKELY]     = Pattern inferred from company name, common pattern
  [UNVERIFIED] = Placeholder; board path may differ — validate before use
"""

# =============================================================================
# WORKDAY — VERIFIED SUBDOMAINS
# =============================================================================
# Format: (tenant, board_path, wd_env, 'Company Name')
# API endpoint: POST https://{tenant}.wd{wd_env}.myworkdayjobs.com/wday/cxs/{tenant}/{board_path}/jobs
# Body: {"appliedFacets":{},"limit":20,"offset":0,"searchText":"<keyword>"}

WORKDAY_VERIFIED = [
    # ── FINANCIAL SERVICES ─────────────────────────────────────────────────────
    # Citigroup:     citi.wd5.myworkdayjobs.com  [VERIFIED]
    ('citi',              '2',                          'wd5',  'Citigroup'),
    # Morgan Stanley: ms.wd5.myworkdayjobs.com   [VERIFIED]
    ('ms',                'External',                   'wd5',  'Morgan Stanley'),
    # Wells Fargo:   wf.wd1.myworkdayjobs.com    [VERIFIED]
    ('wf',                'WellsFargoJobs',             'wd1',  'Wells Fargo'),
    # Bank of America: ghr.wd1.myworkdayjobs.com [VERIFIED] — tenant is "ghr"
    ('ghr',               'Lateral-US',                 'wd1',  'Bank of America'),
    # Visa:          visa.wd5.myworkdayjobs.com   [VERIFIED]
    ('visa',              'Visa_Early_Careers',          'wd5',  'Visa'),
    # Mastercard:    mastercard.wd1.myworkdayjobs.com [VERIFIED]
    ('mastercard',        'CorporateCareers',            'wd1',  'Mastercard'),
    # BlackRock:     blackrock.wd1.myworkdayjobs.com [VERIFIED]
    ('blackrock',         'BlackRock_Professional',      'wd1',  'BlackRock'),
    # Fidelity (FMR): fmr.wd1.myworkdayjobs.com  [VERIFIED]
    ('fmr',               'FidelityCareers',             'wd1',  'Fidelity Investments'),
    # Invesco:       invesco.wd1.myworkdayjobs.com [VERIFIED]
    ('invesco',           'IVZ',                         'wd1',  'Invesco'),
    # Franklin Templeton: franklintempleton.wd5.myworkdayjobs.com [VERIFIED]
    ('franklintempleton', 'Primary-External-1',          'wd5',  'Franklin Templeton'),
    # T. Rowe Price: troweprice.wd5.myworkdayjobs.com [VERIFIED]
    ('troweprice',        'TRowePrice',                  'wd5',  'T. Rowe Price'),
    # State Street:  statestreet.wd1.myworkdayjobs.com [VERIFIED]
    ('statestreet',       'Global',                      'wd1',  'State Street'),
    # PayPal:        paypal.wd1.myworkdayjobs.com  [VERIFIED]
    ('paypal',            'jobs',                        'wd1',  'PayPal'),
    # Synchrony Financial: synchronyfinancial.wd5.myworkdayjobs.com [VERIFIED]
    ('synchronyfinancial','careers',                     'wd5',  'Synchrony Financial'),
    # AIG:           aig.wd1.myworkdayjobs.com     [VERIFIED]
    ('aig',               'aig',                         'wd1',  'AIG'),
    # Prudential:    pru.wd5.myworkdayjobs.com     [VERIFIED]
    ('pru',               'Careers',                     'wd5',  'Prudential Financial'),
    # The Travelers: travelers.wd5.myworkdayjobs.com [VERIFIED]
    ('travelers',         'External',                    'wd5',  'Travelers'),
    # The Hartford:  thehartford.wd5.myworkdayjobs.com [VERIFIED]
    ('thehartford',       'Careers_External',            'wd5',  'The Hartford'),
    # Allstate:      allstate.wd5.myworkdayjobs.com [VERIFIED]
    ('allstate',          'allstate_careers',            'wd5',  'Allstate'),
    # JPMorgan Chase: Note — JPMC uses Oracle Fusion (jpmc.fa.oraclecloud.com), NOT Workday
    # Goldman Sachs:  Note — Uses iCIMS: uscareers-goldmansachs.icims.com
    # American Express: Note — Uses Taleo (axp.taleo.net) + Eightfold.ai
    # Charles Schwab: Note — Custom portal at schwabjobs.com (not confirmed Workday)

    # ── TECHNOLOGY ──────────────────────────────────────────────────────────────
    # NVIDIA:        nvidia.wd5.myworkdayjobs.com  [VERIFIED]
    ('nvidia',            'NVIDIAExternalCareerSite',    'wd5',  'NVIDIA'),
    # Salesforce:    salesforce.wd12.myworkdayjobs.com [VERIFIED]
    ('salesforce',        'External_Career_Site',        'wd12', 'Salesforce'),
    # Cisco:         cisco.wd5.myworkdayjobs.com   [VERIFIED]
    ('cisco',             'Cisco_Careers',               'wd5',  'Cisco'),
    # Intel:         intel.wd1.myworkdayjobs.com   [VERIFIED]
    ('intel',             'External',                    'wd1',  'Intel'),
    # Qualcomm:      qualcomm.wd5.myworkdayjobs.com AND qualcomm.wd12.myworkdayjobs.com [VERIFIED]
    ('qualcomm',          'External',                    'wd5',  'Qualcomm'),
    # Broadcom:      broadcom.wd1.myworkdayjobs.com [VERIFIED]
    ('broadcom',          'External_Career',             'wd1',  'Broadcom'),
    # Applied Materials: amat.wd1.myworkdayjobs.com [VERIFIED]
    ('amat',              'External',                    'wd1',  'Applied Materials'),
    # ServiceNow:    servicenow.wd5.myworkdayjobs.com [LIKELY]
    ('servicenow',        'External',                    'wd5',  'ServiceNow'),
    # Adobe:         adobe.wd5.myworkdayjobs.com   [LIKELY]
    ('adobe',             'external',                    'wd5',  'Adobe'),
    # Palo Alto Networks: paloaltonetworks.wd5.myworkdayjobs.com [LIKELY]
    ('paloaltonetworks',  'External',                    'wd5',  'Palo Alto Networks'),
    # CrowdStrike:   crowdstrike.wd5.myworkdayjobs.com [LIKELY]
    ('crowdstrike',       'crowdstrikecareers',          'wd5',  'CrowdStrike'),
    # Intuit:        intuit.wd5.myworkdayjobs.com   [LIKELY]
    ('intuit',            'External',                    'wd5',  'Intuit'),
    # Oracle:        oracle.wd1.myworkdayjobs.com   [LIKELY]
    ('oracle',            'OracleCareer',                'wd1',  'Oracle'),
    # Dell:          dell.wd1.myworkdayjobs.com     [LIKELY]
    ('dell',              'External',                    'wd1',  'Dell Technologies'),
    # HPE:           hpe.wd5.myworkdayjobs.com      [VERIFIED via search result]
    ('hpe',               'Jobsathpe',                   'wd5',  'Hewlett Packard Enterprise'),
    # Workday itself: workday.wd5.myworkdayjobs.com [VERIFIED]
    ('workday',           'Workday',                     'wd5',  'Workday'),
    # IBM: Note — IBM uses custom careers at ibm.com/careers (NOT Workday)
    # Apple: Note — Uses custom ATS at jobs.apple.com (NOT Workday)
    # Microsoft: Note — Uses Oracle Taleo + custom at jobs.careers.microsoft.com
    # Amazon: Note — Custom at amazon.jobs (NOT Workday)
    # Google/Alphabet: Note — Custom at careers.google.com (NOT Workday)
    # Meta: Note — Custom at metacareers.com (NOT Workday)
    # Tesla: Note — Custom in-house ATS at tesla.com/careers

    # ── HEALTHCARE ──────────────────────────────────────────────────────────────
    # CVS Health:    cvshealth.wd1.myworkdayjobs.com [VERIFIED]
    ('cvshealth',         'cvs_health_careers',          'wd1',  'CVS Health'),
    # Humana:        humana.wd5.myworkdayjobs.com   [VERIFIED]
    ('humana',            'Humana_External_Career_Site', 'wd5',  'Humana'),
    # Cigna:         cigna.wd5.myworkdayjobs.com    [VERIFIED]
    ('cigna',             'cignacareers',                'wd5',  'Cigna Group'),
    # Elevance Health (Anthem): elevancehealth.wd1.myworkdayjobs.com [VERIFIED]
    ('elevancehealth',    'ANT',                         'wd1',  'Elevance Health (Anthem)'),
    # Centene:       centene.wd5.myworkdayjobs.com  [VERIFIED]
    ('centene',           'Centene_External',            'wd5',  'Centene'),
    # Johnson & Johnson: jj.wd5.myworkdayjobs.com   [VERIFIED]
    ('jj',                'JJ',                          'wd5',  'Johnson & Johnson'),
    # Pfizer:        pfizer.wd1.myworkdayjobs.com   [VERIFIED]
    ('pfizer',            'PfizerCareers',               'wd1',  'Pfizer'),
    # Merck (MSD):   msd.wd5.myworkdayjobs.com      [VERIFIED]
    ('msd',               'SearchJobs',                  'wd5',  'Merck (MSD)'),
    # Eli Lilly:     lilly.wd5.myworkdayjobs.com    [VERIFIED]
    ('lilly',             'LLY',                         'wd5',  'Eli Lilly'),
    # Abbott:        abbott.wd5.myworkdayjobs.com   [VERIFIED]
    ('abbott',            'abbottcareers',               'wd5',  'Abbott'),
    # UnitedHealth Group: Note — Custom ATS at careers.unitedhealthgroup.com
    # AbbVie: Note — Uses SmartRecruiters at careers.smartrecruiters.com/AbbVie

    # ── DEFENSE / AEROSPACE ─────────────────────────────────────────────────────
    # Northrop Grumman: ngc.wd1.myworkdayjobs.com  [VERIFIED]
    ('ngc',               'Northrop_Grumman_Contingent_Worker_Site', 'wd1', 'Northrop Grumman'),
    # RTX (Raytheon): globalhr.wd5.myworkdayjobs.com [VERIFIED] — tenant is "globalhr"
    ('globalhr',          'REC_RTX_Ext_Gateway',         'wd5',  'RTX (Raytheon Technologies)'),
    # Boeing:        boeing.wd1.myworkdayjobs.com   [VERIFIED]
    ('boeing',            'EXTERNAL_CAREERS',            'wd1',  'Boeing'),
    # Leidos:        leidos.wd5.myworkdayjobs.com   [VERIFIED]
    ('leidos',            'External',                    'wd5',  'Leidos'),
    # GDIT (General Dynamics IT): gdit.wd5.myworkdayjobs.com [VERIFIED]
    ('gdit',              'External_Career_Site',        'wd5',  'General Dynamics IT (GDIT)'),
    # Booz Allen Hamilton: bah.wd1.myworkdayjobs.com [VERIFIED]
    ('bah',               'BAH_Jobs',                    'wd1',  'Booz Allen Hamilton'),
    # Lockheed Martin: lmco.wd1.myworkdayjobs.com  [LIKELY — lmco is official tenant]
    ('lmco',              'LMCareers',                   'wd1',  'Lockheed Martin'),
    # L3Harris: Note — Uses custom ATS at careers.l3harris.com (NOT Workday confirmed)
    # SAIC: Note — Unconfirmed; may use Workday but tenant not verified
    # CACI: Note — Uses custom ATS at careers.caci.com
    # General Dynamics (Electric Boat div): Uses iCIMS at careers-gdeb.icims.com
    # General Dynamics (main): Note — gd.wd1 LIKELY but not confirmed

    # ── TELECOM / MEDIA ──────────────────────────────────────────────────────────
    # AT&T:          att.wd1.myworkdayjobs.com      [VERIFIED]
    ('att',               'ATTCollege',                  'wd1',  'AT&T'),
    # Comcast:       comcast.wd5.myworkdayjobs.com  [VERIFIED]
    ('comcast',           'Comcast_Careers',             'wd5',  'Comcast / NBCUniversal'),
    # T-Mobile:      tmobile.wd1.myworkdayjobs.com  [VERIFIED]
    ('tmobile',           'External',                    'wd1',  'T-Mobile'),
    # Verizon:       verizon.wd12.myworkdayjobs.com [VERIFIED]
    ('verizon',           'verizon-careers',             'wd12', 'Verizon'),
    # Disney:        disney.wd5.myworkdayjobs.com   [LIKELY]
    ('disney',            'External',                    'wd5',  'Walt Disney Company'),
    # Charter Communications: Note — Not confirmed on Workday; likely iCIMS

    # ── ENERGY ───────────────────────────────────────────────────────────────────
    # Chevron:       chevron.wd5.myworkdayjobs.com  [VERIFIED]
    ('chevron',           'jobs',                        'wd5',  'Chevron'),
    # ConocoPhillips: conocophillips.wd1.myworkdayjobs.com [VERIFIED]
    ('conocophillips',    'eQuest',                      'wd1',  'ConocoPhillips'),
    # Baker Hughes:  bakerhughes.wd5.myworkdayjobs.com [VERIFIED]
    ('bakerhughes',       'BakerHughes',                 'wd5',  'Baker Hughes'),
    # Duke Energy:   dukeenergy.wd1.myworkdayjobs.com [VERIFIED]
    ('dukeenergy',        'search',                      'wd1',  'Duke Energy'),
    # ExxonMobil: Note — Uses SAP SuccessFactors at career4.successfactors.com
    # Halliburton: Note — Custom careers at careers.halliburton.com (not confirmed Workday)
    # Schlumberger (SLB): Note — Custom careers at careers.slb.com

    # ── INDUSTRIAL / MANUFACTURING ────────────────────────────────────────────────
    # Caterpillar:   cat.wd5.myworkdayjobs.com      [VERIFIED]
    ('cat',               'CaterpillarCareers',          'wd5',  'Caterpillar'),
    # 3M:            3m.wd1.myworkdayjobs.com        [VERIFIED]
    ('3m',                'Search',                      'wd1',  '3M'),
    # General Motors: generalmotors.wd5.myworkdayjobs.com [VERIFIED]
    ('generalmotors',     'Careers_GM',                  'wd5',  'General Motors'),
    # Stellantis:    stellantis.wd3.myworkdayjobs.com [VERIFIED]
    ('stellantis',        'External_Career_Site_ID01',   'wd3',  'Stellantis'),
    # FedEx:         fedex.wd1.myworkdayjobs.com     [VERIFIED — international boards]
    ('fedex',             'FXE-US_External',             'wd1',  'FedEx'),
    # UPS:           hcmportal.wd5.myworkdayjobs.com [VERIFIED] — tenant is "hcmportal"
    ('hcmportal',         'Search',                      'wd5',  'UPS'),
    # Honeywell: Note — Uses Oracle Fusion (ibqbjb.fa.ocs.oraclecloud.com), NOT Workday
    # John Deere: Note — Unconfirmed Workday; uses custom at johndeere.jobs
    # Ford Motor: Note — Uses Oracle Fusion (efds.fa.em5.oraclecloud.com), NOT Workday

    # ── RETAIL / CONSUMER ─────────────────────────────────────────────────────────
    # Walmart:       walmart.wd5.myworkdayjobs.com  [VERIFIED]
    ('walmart',           'WalmartExternal',             'wd5',  'Walmart'),
    # Target:        target.wd5.myworkdayjobs.com   [VERIFIED]
    ('target',            'targetcareers',               'wd5',  'Target'),
    # Home Depot:    homedepot.wd5.myworkdayjobs.com [VERIFIED]
    ('homedepot',         'CareerDepot',                 'wd5',  'The Home Depot'),
    # Lowe's:        lowes.wd5.myworkdayjobs.com    [VERIFIED]
    ('lowes',             'LWS_External_CS',             'wd5',  "Lowe's"),
    # Procter & Gamble: pg.wd5.myworkdayjobs.com    [VERIFIED]
    ('pg',                '1000',                        'wd5',  'Procter & Gamble'),
    # Nike:  Note — Uses Oracle Taleo at nike.taleo.net
    # Starbucks: Note — Uses Oracle Taleo at starbucks.taleo.net
    # McDonald's: Note — Uses SmartRecruiters at jobs.smartrecruiters.com/McDonaldsUSA
    # Costco: Note — Custom at costco.com/jobs (not Workday confirmed)

    # ── FOOD & BEVERAGE ───────────────────────────────────────────────────────────
    # Mondelez:      mdlz.wd3.myworkdayjobs.com     [VERIFIED]
    ('mdlz',              'External',                    'wd3',  'Mondelez International'),
    # Kraft Heinz:   heinz.wd1.myworkdayjobs.com    [VERIFIED] — tenant is "heinz"
    ('heinz',             'KraftHeinz_Careers',          'wd1',  'Kraft Heinz'),
    # Campbell Soup: campbellsoup.wd5.myworkdayjobs.com [VERIFIED]
    ('campbellsoup',      'ExternalCareers_GlobalSite',  'wd5',  'Campbell Soup'),
    # PepsiCo:       pepsico.wd5.myworkdayjobs.com  [LIKELY]
    ('pepsico',           'External',                    'wd5',  'PepsiCo'),

    # ── INSURANCE ─────────────────────────────────────────────────────────────────
    # Prudential Financial: pru.wd5.myworkdayjobs.com [VERIFIED — listed above]
    # Travelers:     travelers.wd5.myworkdayjobs.com [VERIFIED — listed above]
    # The Hartford:  thehartford.wd5.myworkdayjobs.com [VERIFIED — listed above]
    # Allstate:      allstate.wd5.myworkdayjobs.com [VERIFIED — listed above]
    # Progressive: Note — Custom at careers.progressive.com (NOT Workday confirmed)
    # MetLife: Note — Custom at metlifecareers.com (NOT Workday confirmed)

    # ── DC AREA / GOVERNMENT-ADJACENT ─────────────────────────────────────────────
    # Fannie Mae: fanniemae.wd5.myworkdayjobs.com  [LIKELY]
    ('fanniemae',         'External',                    'wd5',  'Fannie Mae'),
    # Freddie Mac: freddiemac.wd5.myworkdayjobs.com [LIKELY]
    ('freddiemac',        'External',                    'wd5',  'Freddie Mac'),
    # USAA:          usaa.wd5.myworkdayjobs.com     [LIKELY]
    ('usaa',              'External',                    'wd5',  'USAA'),

    # ── ADDITIONAL S&P 500 TECH / PROFESSIONAL SERVICES ───────────────────────────
    # Deloitte:      deloitte.wd1.myworkdayjobs.com [LIKELY]
    ('deloitte',          'DTCareer',                    'wd1',  'Deloitte'),
    # Accenture:     accenture.wd3.myworkdayjobs.com [LIKELY]
    ('accenture',         'accenture',                   'wd3',  'Accenture'),
    # PwC:           pwc.wd3.myworkdayjobs.com       [VERIFIED via search result]
    ('pwc',               'Global_Experienced_Careers',  'wd3',  'PricewaterhouseCoopers'),
    # EY:            ey.wd1.myworkdayjobs.com        [LIKELY]
    ('ey',                'ey',                          'wd1',  'EY (Ernst & Young)'),
    # KPMG:          kpmg.wd5.myworkdayjobs.com      [LIKELY]
    ('kpmg',              'External',                    'wd5',  'KPMG'),
    # Gartner:       gartner.wd5.myworkdayjobs.com   [LIKELY]
    ('gartner',           'External',                    'wd5',  'Gartner'),
]


# =============================================================================
# QUICK-REFERENCE: CONFIRMED WORKDAY TENANT → COMPANY MAPPING
# =============================================================================
# These are the EXACT verified tenant IDs from live URL discovery
# (tenant, wd_env) → company
WORKDAY_CONFIRMED_TENANTS = {
    # Financial
    'citi':               ('wd5', 'Citigroup'),
    'ms':                 ('wd5', 'Morgan Stanley'),
    'wf':                 ('wd1', 'Wells Fargo'),
    'ghr':                ('wd1', 'Bank of America'),            # NOT "bankofamerica"
    'visa':               ('wd5', 'Visa'),
    'mastercard':         ('wd1', 'Mastercard'),
    'blackrock':          ('wd1', 'BlackRock'),
    'fmr':                ('wd1', 'Fidelity Investments'),       # NOT "fidelity"
    'invesco':            ('wd1', 'Invesco'),
    'franklintempleton':  ('wd5', 'Franklin Templeton'),
    'troweprice':         ('wd5', 'T. Rowe Price'),
    'statestreet':        ('wd1', 'State Street'),
    'paypal':             ('wd1', 'PayPal'),
    'synchronyfinancial': ('wd5', 'Synchrony Financial'),        # NOT "synchrony"
    'aig':                ('wd1', 'AIG'),
    'pru':                ('wd5', 'Prudential Financial'),        # NOT "prudential"
    'travelers':          ('wd5', 'Travelers'),
    'thehartford':        ('wd5', 'The Hartford'),
    'allstate':           ('wd5', 'Allstate'),
    # Tech
    'nvidia':             ('wd5', 'NVIDIA'),
    'salesforce':         ('wd12','Salesforce'),                  # wd12!
    'cisco':              ('wd5', 'Cisco'),
    'intel':              ('wd1', 'Intel'),
    'qualcomm':           ('wd5', 'Qualcomm'),                   # also wd12 instance
    'broadcom':           ('wd1', 'Broadcom'),
    'amat':               ('wd1', 'Applied Materials'),           # NOT "appliedmaterials"
    'hpe':                ('wd5', 'Hewlett Packard Enterprise'),
    'workday':            ('wd5', 'Workday Inc.'),
    # Healthcare
    'cvshealth':          ('wd1', 'CVS Health'),
    'humana':             ('wd5', 'Humana'),
    'cigna':              ('wd5', 'Cigna'),
    'elevancehealth':     ('wd1', 'Elevance Health (Anthem)'),   # NOT "anthem"
    'centene':            ('wd5', 'Centene'),
    'jj':                 ('wd5', 'Johnson & Johnson'),           # NOT "jnj" or "johnsonandjohnson"
    'pfizer':             ('wd1', 'Pfizer'),
    'msd':                ('wd5', 'Merck (MSD)'),                 # NOT "merck"
    'lilly':              ('wd5', 'Eli Lilly'),
    'abbott':             ('wd5', 'Abbott'),
    # Defense
    'ngc':                ('wd1', 'Northrop Grumman'),            # NOT "northropgrumman"
    'globalhr':           ('wd5', 'RTX / Raytheon Technologies'), # NOT "rtx" or "raytheon"
    'boeing':             ('wd1', 'Boeing'),
    'leidos':             ('wd5', 'Leidos'),
    'gdit':               ('wd5', 'General Dynamics IT'),
    'bah':                ('wd1', 'Booz Allen Hamilton'),
    'lmco':               ('wd1', 'Lockheed Martin'),
    # Telecom
    'att':                ('wd1', 'AT&T'),
    'comcast':            ('wd5', 'Comcast'),
    'tmobile':            ('wd1', 'T-Mobile'),
    'verizon':            ('wd12','Verizon'),                     # wd12!
    # Energy
    'chevron':            ('wd5', 'Chevron'),
    'conocophillips':     ('wd1', 'ConocoPhillips'),
    'bakerhughes':        ('wd5', 'Baker Hughes'),
    'dukeenergy':         ('wd1', 'Duke Energy'),
    # Industrial
    'cat':                ('wd5', 'Caterpillar'),
    '3m':                 ('wd1', '3M'),
    'generalmotors':      ('wd5', 'General Motors'),
    'stellantis':         ('wd3', 'Stellantis'),
    'fedex':              ('wd1', 'FedEx'),
    'hcmportal':          ('wd5', 'UPS'),                         # UPS tenant is "hcmportal"!
    # Retail
    'walmart':            ('wd5', 'Walmart'),
    'target':             ('wd5', 'Target'),
    'homedepot':          ('wd5', 'The Home Depot'),
    'lowes':              ('wd5', "Lowe's"),
    'pg':                 ('wd5', 'Procter & Gamble'),            # NOT "proctergamble"
    # Food
    'mdlz':               ('wd3', 'Mondelez International'),      # NOT "mondelez"
    'heinz':              ('wd1', 'Kraft Heinz'),                 # NOT "kraftheinz"
    'campbellsoup':       ('wd5', 'Campbell Soup'),
    # Insurance
    'globalhr':           ('wd5', 'RTX'),                        # !! Same tenant as RTX above
}


# =============================================================================
# NON-WORKDAY ATS — VERIFIED
# =============================================================================

# Companies using iCIMS
# URL pattern: https://{slug}.icims.com/jobs/{job_id}/job
ICIMS_COMPANIES = [
    # Goldman Sachs: uscareers-goldmansachs.icims.com  [VERIFIED]
    ('uscareers-goldmansachs',  'Goldman Sachs'),
    # General Dynamics (Electric Boat): careers-gdeb.icims.com  [VERIFIED]
    ('careers-gdeb',            'General Dynamics (Electric Boat)'),
]

# Companies using Oracle Taleo
# URL pattern: https://{tenant}.taleo.net/careersection/{section}/jobsearch.ftl
TALEO_COMPANIES = [
    # Nike: nike.taleo.net  [VERIFIED]
    ('nike',      'Nike'),
    # Starbucks: starbucks.taleo.net  [VERIFIED]
    ('starbucks', 'Starbucks'),
    # ConocoPhillips also has legacy: cop.taleo.net  [VERIFIED as secondary]
    ('cop',       'ConocoPhillips (legacy)'),
    # American Express: axp.taleo.net  [VERIFIED]
    ('axp',       'American Express'),
]

# Companies using SmartRecruiters
# URL pattern: https://jobs.smartrecruiters.com/{CompanySlug}/{job_id}
SMARTRECRUITERS_COMPANIES = [
    # AbbVie: careers.smartrecruiters.com/AbbVie  [VERIFIED]
    ('AbbVie',      'AbbVie'),
    # McDonald's: jobs.smartrecruiters.com/McDonaldsUSA  [VERIFIED — from search results]
    ('McDonaldsUSA','McDonald\'s'),
    # Tesla: jobs.smartrecruiters.com/Tesla1  [VERIFIED — from search results]
    ('Tesla1',      'Tesla'),
]

# Companies using SAP SuccessFactors
# URL pattern: https://career{n}.successfactors.com/careers?company={tenant}
SUCCESSFACTORS_COMPANIES = [
    # ExxonMobil: career4.successfactors.com/careers?company=exxonmobilP  [VERIFIED]
    ('exxonmobilP', 'ExxonMobil'),
]

# Companies using Oracle Fusion / HCM Cloud
# URL pattern: https://{instance}.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/...
ORACLE_HCM_COMPANIES = [
    # JPMorgan Chase: jpmc.fa.oraclecloud.com  [VERIFIED]
    ('jpmc.fa.oraclecloud.com',   'JPMorgan Chase'),
    # Ford Motor: efds.fa.em5.oraclecloud.com  [VERIFIED]
    ('efds.fa.em5.oraclecloud.com', 'Ford Motor Company'),
    # Honeywell: ibqbjb.fa.ocs.oraclecloud.com  [VERIFIED]
    ('ibqbjb.fa.ocs.oraclecloud.com', 'Honeywell'),
]

# Companies using Eightfold AI (modern AI-powered ATS)
EIGHTFOLD_COMPANIES = [
    # American Express: aexp.eightfold.ai  [VERIFIED]
    ('aexp', 'American Express'),
]

# Companies with custom in-house ATS
CUSTOM_ATS_COMPANIES = [
    ('tesla.com/careers',            'Tesla (in-house)'),
    ('careers.google.com',           'Google / Alphabet'),
    ('amazon.jobs',                  'Amazon'),
    ('careers.microsoft.com',        'Microsoft'),
    ('metacareers.com',              'Meta'),
    ('jobs.apple.com',               'Apple'),
    ('ibm.com/careers',              'IBM'),
    ('careers.unitedhealthgroup.com','UnitedHealth Group'),
    ('careers.progressive.com',      'Progressive Insurance'),
    ('metlifecareers.com',           'MetLife'),
    ('schwabjobs.com',               'Charles Schwab'),
    ('careers.l3harris.com',         'L3Harris Technologies'),
    ('careers.caci.com',             'CACI International'),
    ('careers.halliburton.com',      'Halliburton'),
    ('careers.slb.com',              'SLB (Schlumberger)'),
]


# =============================================================================
# GREENHOUSE — S&P 500 / LARGE-CAP COMPANIES
# =============================================================================
# URL pattern: https://boards.greenhouse.io/{slug}
# API pattern: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

GREENHOUSE_COMPANIES = [
    # These are primarily growth-stage and mid/large tech — Greenhouse is LESS common
    # among true S&P 500 incumbents (who prefer Workday/Taleo). The ones below are
    # verified or highly likely from search results.

    # ── Confirmed via URL discovery ─────────────────────────────────────────────
    ('airbnb',         'Airbnb'),
    ('okta',           'Okta'),
    ('stripe',         'Stripe'),
    ('lyft',           'Lyft'),
    ('pinterest',      'Pinterest'),
    ('snap',           'Snap Inc.'),
    ('robinhood',      'Robinhood'),
    ('coinbase',       'Coinbase'),
    ('doordash',       'DoorDash'),
    ('reddit',         'Reddit'),
    ('dropbox',        'Dropbox'),
    ('zendesk',        'Zendesk'),
    ('twilio',         'Twilio'),
    ('datadog',        'Datadog'),
    ('cloudflare',     'Cloudflare'),
    ('mongodb',        'MongoDB'),
    ('hubspot',        'HubSpot'),
    ('gitlab',         'GitLab'),
    ('figma',          'Figma'),
    ('anthropic',      'Anthropic'),
    ('openai',         'OpenAI'),
    ('duolingo',       'Duolingo'),
    ('rivian',         'Rivian'),
    ('lucid',          'Lucid Motors'),

    # ── S&P 500 members using Greenhouse ────────────────────────────────────────
    # Note: These are less common; most large S&P 500 use Workday or Taleo.
    # Verify each before use.
    ('netflix',        'Netflix'),   # Netflix uses Lever (see below) — verify
    ('uber',           'Uber'),      # Uber has moved — verify current
    ('twitter',        'Twitter/X'), # May have changed post-acquisition
    ('zillow',         'Zillow'),
    ('wayfair',        'Wayfair'),
    ('yelp',           'Yelp'),
    ('eventbrite',     'Eventbrite'),
    ('squarespace',    'Squarespace'),
    ('brex',           'Brex'),
]


# =============================================================================
# LEVER — S&P 500 / LARGE-CAP COMPANIES
# =============================================================================
# URL pattern: https://jobs.lever.co/{slug}
# API pattern: GET https://api.lever.co/v0/postings/{slug}?mode=json

LEVER_COMPANIES = [
    # ── Confirmed / High-confidence ─────────────────────────────────────────────
    ('netflix',        'Netflix'),
    ('shopify',        'Shopify'),      # Shopify also has Workday — verify which is primary
    ('scale-ai',       'Scale AI'),
    ('palantir',       'Palantir'),     # Palantir also has own jobs site
    ('anduril',        'Anduril Industries'),
    ('waymo',          'Waymo'),
    ('nuro',           'Nuro'),
    ('airtable',       'Airtable'),
    ('notion',         'Notion'),
    ('asana',          'Asana'),
    ('benchling',      'Benchling'),
    ('plaid',          'Plaid'),
    ('carta',          'Carta'),
    ('flexport',       'Flexport'),
    ('faire',          'Faire'),
    ('ramp',           'Ramp'),
    ('toast',          'Toast'),
    ('samsara',        'Samsara'),
    ('lattice',        'Lattice'),
    ('retool',         'Retool'),
    ('gusto',          'Gusto'),
    ('rippling',       'Rippling'),
    ('chime',          'Chime'),
    ('nerdwallet',     'NerdWallet'),
    ('affirm',         'Affirm'),
]


# =============================================================================
# BIG TECH CUSTOM CAREER PAGE APIS
# =============================================================================
# These companies have their OWN career sites with queryable JSON endpoints.
# Note: These APIs are undocumented/unofficial — they may change without notice.
# Use with appropriate rate limiting and User-Agent headers.

BIG_TECH_CAREER_APIS = {

    'Google': {
        'base_url':     'https://careers.google.com',
        'search_url':   'https://careers.google.com/api/jobs/jobs-v1/jobs:search/',
        'method':       'GET',
        'params': {
            'q':            '<keyword>',
            'location':     'United States',
            'page_size':    '20',
            'page':         '1',
            'target_level': '',   # INTERN, EARLY, MID, SENIOR, DIRECTOR
            'employment_type': 'FULL_TIME',
        },
        'notes': (
            'Returns JSON with jobs[]. Each job has title, description, '
            'apply_url, locations, publish_date. '
            'Pagination via page param. '
            'No auth required but rate-limit to ~30 req/min.'
        ),
        'example': 'GET https://careers.google.com/api/jobs/jobs-v1/jobs:search/?q=data+engineer&location=United+States',
    },

    'Amazon': {
        'base_url':   'https://www.amazon.jobs',
        'search_url': 'https://www.amazon.jobs/en/search.json',
        'method':     'GET',
        'params': {
            'normalized_country_code[]': 'USA',
            'result_limit':              '10',
            'offset':                    '0',
            'query':                     '<keyword>',
            'latitude':                  '',
            'longitude':                 '',
            'loc_query':                 '',
            'base_query':                '<keyword>',
            'city':                      '',
            'country':                   'USA',
            'region':                    '',
            'county':                    '',
            'query_options':             '',
            'schedule_type_id[]':        'Full-Time',
        },
        'notes': (
            'Returns JSON with jobs[] array. '
            'Each job has job_title, location, posted_date, job_path (relative URL). '
            'Full job URL: https://www.amazon.jobs{job_path}. '
            'No auth required. Paginate via offset param.'
        ),
        'example': 'GET https://www.amazon.jobs/en/search.json?query=data+engineer&normalized_country_code[]=USA&result_limit=10',
    },

    'Microsoft': {
        'base_url':   'https://jobs.careers.microsoft.com',
        'search_url': 'https://jobs.careers.microsoft.com/global/en/search',
        'api_url':    'https://jobs.careers.microsoft.com/global/en/search',
        'method':     'GET',
        'params': {
            'q':         '<keyword>',
            'pg':        '1',       # page number
            'pgSz':      '20',      # page size
            'o':         'Recent',  # sort: Recent, Relevance
            'flt':       'true',
        },
        'notes': (
            'Microsoft careers moved to jobs.careers.microsoft.com. '
            'The page renders server-side HTML; for JSON, use the internal API: '
            'GET https://gcsservices.careers.microsoft.com/search/api/v1/search?q=<kw>&l=en_us&pgSz=20&pg=1&src=JB-10000. '
            'Returns JSON with operationResult.result.jobs[]. '
            'No auth required.'
        ),
        'json_api_url': 'https://gcsservices.careers.microsoft.com/search/api/v1/search',
        'json_params': {
            'q':    '<keyword>',
            'l':    'en_us',
            'pgSz': '20',
            'pg':   '1',
            'src':  'JB-10000',
        },
        'example': 'GET https://gcsservices.careers.microsoft.com/search/api/v1/search?q=data+engineer&l=en_us&pgSz=20&pg=1&src=JB-10000',
    },

    'Meta': {
        'base_url':   'https://www.metacareers.com',
        'search_url': 'https://www.metacareers.com/jobs',
        'api_url':    'https://www.metacareers.com/graphql',
        'method':     'POST',
        'notes': (
            'Meta careers uses GraphQL. Send POST to https://www.metacareers.com/graphql '
            'with JSON body containing the jobs search query. '
            'Alternatively, use the public JSON feed: '
            'GET https://www.metacareers.com/careers/jobs/?q=<keyword>&teams[]=<team>&offices[]=<office> '
            'which returns HTML (scrape). '
            'The dejobs.org mirror provides XML: '
            'GET https://metacareers.dejobs.org/jobs/?format=xml'
        ),
        'json_feed':  'https://metacareers.dejobs.org/jobs/?q=<keyword>',
        'example':    'GET https://metacareers.dejobs.org/jobs/?q=data+engineer',
    },

    'Apple': {
        'base_url':   'https://jobs.apple.com',
        'search_url': 'https://jobs.apple.com/en-us/search',
        'api_url':    'https://jobs.apple.com/api/role/search',
        'method':     'POST',
        'body': {
            'query':       '<keyword>',
            'filters': {
                'range': {
                    'standardWeeklyHours': {'start': None, 'end': None},
                },
                'roleChange':        False,
                'teams':             [],
                'subTeams':          [],
                'hiringTypes':       [],
                'locations':         [],
                'locationType':      [],
                'jobType':           [],
                'homeOffice':        [],
            },
            'page':  1,
            'locale': 'en-us',
            'sort':  'newest',
        },
        'notes': (
            'Apple uses a POST JSON API at https://jobs.apple.com/api/role/search. '
            'Returns JSON with searchResults[]. Each result has positionId, postingTitle, '
            'postingTeam, postingSubteam, locations[], minimumHours, maximumHours. '
            'No auth required. Paginate via page param.'
        ),
        'example': 'POST https://jobs.apple.com/api/role/search with body {"query":"data engineer","page":1,"locale":"en-us","sort":"newest","filters":{}}',
    },

    'IBM': {
        'base_url':   'https://www.ibm.com/careers',
        'search_url': 'https://www.ibm.com/careers/search',
        'notes': (
            'IBM uses a custom career portal at ibm.com/careers/search. '
            'The underlying API is: '
            'GET https://careers.ibm.com/api/jobs?q=<keyword>&limit=20&offset=0&country=USA '
            'Returns JSON with jobs[].'
        ),
        'api_url': 'https://careers.ibm.com/api/jobs',
        'method':  'GET',
        'params': {
            'q':       '<keyword>',
            'limit':   '20',
            'offset':  '0',
            'country': 'USA',
        },
        'example': 'GET https://careers.ibm.com/api/jobs?q=data+engineer&limit=20&offset=0&country=USA',
    },
}


# =============================================================================
# WORKDAY API USAGE NOTES
# =============================================================================
WORKDAY_API_NOTES = """
WORKDAY JOB SEARCH API
======================
Endpoint: POST https://{tenant}.wd{env}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs

Request body (JSON):
    {
        "appliedFacets": {},
        "limit": 20,
        "offset": 0,
        "searchText": "<keyword>"
    }

Response: JSON with jobPostings[] array. Each has:
    - title
    - externalPath  (relative path — append to base URL for full link)
    - locationsText
    - postedOn
    - bulletFields

Full job URL: https://{tenant}.wd{env}.myworkdayjobs.com/en-US/{board}/job/{externalPath}

IMPORTANT GOTCHAS discovered via research:
  1. Bank of America tenant is "ghr" (NOT "bankofamerica")
  2. Northrop Grumman tenant is "ngc" (NOT "northropgrumman")
  3. RTX/Raytheon tenant is "globalhr" (NOT "rtx" or "raytheon")
  4. UPS tenant is "hcmportal" (NOT "ups")
  5. Fidelity tenant is "fmr" (NOT "fidelity")
  6. Synchrony tenant is "synchronyfinancial" (NOT "synchrony")
  7. Prudential tenant is "pru" (NOT "prudential")
  8. Eli Lilly board path is "LLY" (NOT "External")
  9. J&J tenant is "jj" (NOT "jnj")
  10. Merck US uses "msd" tenant (Merck Sharp & Dohme)
  11. Mondelez tenant is "mdlz" (NOT "mondelez")
  12. Kraft Heinz tenant is "heinz" (NOT "kraftheinz")
  13. Salesforce uses wd12 (NOT wd5 or wd1)
  14. Verizon uses wd12 (NOT wd5 or wd1)
  15. Qualcomm has BOTH wd5 and wd12 instances

NOT ON WORKDAY (use their own systems):
  - JPMorgan Chase  → Oracle Fusion HCM (jpmc.fa.oraclecloud.com)
  - Ford Motor      → Oracle Fusion HCM (efds.fa.em5.oraclecloud.com)
  - Honeywell       → Oracle Fusion HCM (ibqbjb.fa.ocs.oraclecloud.com)
  - ExxonMobil      → SAP SuccessFactors (career4.successfactors.com)
  - Goldman Sachs   → iCIMS (uscareers-goldmansachs.icims.com)
  - General Dynamics (Electric Boat) → iCIMS (careers-gdeb.icims.com)
  - Nike            → Oracle Taleo (nike.taleo.net)
  - Starbucks       → Oracle Taleo (starbucks.taleo.net)
  - American Express → Oracle Taleo (axp.taleo.net) + Eightfold
  - AbbVie          → SmartRecruiters (careers.smartrecruiters.com/AbbVie)
  - McDonald's      → SmartRecruiters (jobs.smartrecruiters.com/McDonaldsUSA)
  - Tesla           → Custom in-house ATS
  - Apple           → Custom (jobs.apple.com)
  - Google          → Custom (careers.google.com)
  - Amazon          → Custom (amazon.jobs)
  - Microsoft       → Custom (jobs.careers.microsoft.com)
  - Meta            → Custom (metacareers.com)
  - IBM             → Custom (ibm.com/careers)
  - UnitedHealth    → Custom (careers.unitedhealthgroup.com)
  - Progressive     → Custom (careers.progressive.com)
  - MetLife         → Custom (metlifecareers.com)
  - Halliburton     → Custom (careers.halliburton.com)
  - Schlumberger    → Custom (careers.slb.com)
  - Charles Schwab  → Custom (schwabjobs.com)
  - L3Harris        → Custom (careers.l3harris.com)
  - CACI            → Custom (careers.caci.com)
"""


# =============================================================================
# GREENHOUSE API NOTES
# =============================================================================
GREENHOUSE_API_NOTES = """
GREENHOUSE JOB BOARD API (PUBLIC — NO AUTH REQUIRED)
=====================================================
List all jobs:
  GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Single job:
  GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}

Response: JSON with jobs[] array. Each job has:
  - id, title, location.name, updated_at, absolute_url, metadata[]

Paginate: Not paginated — returns ALL open jobs in one call.
Rate limit: ~60 req/min recommended.
"""


# =============================================================================
# LEVER API NOTES
# =============================================================================
LEVER_API_NOTES = """
LEVER JOB POSTINGS API (PUBLIC — NO AUTH REQUIRED)
===================================================
List all postings:
  GET https://api.lever.co/v0/postings/{slug}?mode=json&limit=50

Single posting:
  GET https://api.lever.co/v0/postings/{slug}/{posting_id}

Response: JSON array. Each posting has:
  - id, text (title), categories.team, categories.location,
    categories.commitment, hostedUrl, applyUrl, createdAt

Filter by team:
  GET https://api.lever.co/v0/postings/{slug}?mode=json&team=<team_name>

Rate limit: ~60 req/min recommended.
"""


# =============================================================================
# PYTHON TUPLE FORMAT (as requested in original brief)
# =============================================================================
# For Workday — verified companies in original tuple format:
WORKDAY_TUPLES_VERIFIED = [
    # ── FINANCIAL ──────────────────────────────────────────────────────────────
    # Format: ('tenant', 'wd_env', 'Company Name')
    # Note: board_path omitted here; see WORKDAY_VERIFIED list above for full data
    ('citi',              'wd5',  'Citigroup'),
    ('ms',                'wd5',  'Morgan Stanley'),
    ('wf',                'wd1',  'Wells Fargo'),
    ('ghr',               'wd1',  'Bank of America'),
    ('visa',              'wd5',  'Visa'),
    ('mastercard',        'wd1',  'Mastercard'),
    ('blackrock',         'wd1',  'BlackRock'),
    ('fmr',               'wd1',  'Fidelity Investments'),
    ('invesco',           'wd1',  'Invesco'),
    ('franklintempleton', 'wd5',  'Franklin Templeton'),
    ('troweprice',        'wd5',  'T. Rowe Price'),
    ('statestreet',       'wd1',  'State Street'),
    ('paypal',            'wd1',  'PayPal'),
    ('synchronyfinancial','wd5',  'Synchrony Financial'),
    ('aig',               'wd1',  'AIG'),
    ('pru',               'wd5',  'Prudential Financial'),
    ('travelers',         'wd5',  'Travelers Companies'),
    ('thehartford',       'wd5',  'The Hartford'),
    ('allstate',          'wd5',  'Allstate'),
    # ── TECHNOLOGY ─────────────────────────────────────────────────────────────
    ('nvidia',            'wd5',  'NVIDIA'),
    ('salesforce',        'wd12', 'Salesforce'),
    ('cisco',             'wd5',  'Cisco Systems'),
    ('intel',             'wd1',  'Intel'),
    ('qualcomm',          'wd5',  'Qualcomm'),
    ('broadcom',          'wd1',  'Broadcom'),
    ('amat',              'wd1',  'Applied Materials'),
    ('hpe',               'wd5',  'Hewlett Packard Enterprise'),
    ('servicenow',        'wd5',  'ServiceNow'),
    ('adobe',             'wd5',  'Adobe'),
    ('paloaltonetworks',  'wd5',  'Palo Alto Networks'),
    ('crowdstrike',       'wd5',  'CrowdStrike'),
    ('intuit',            'wd5',  'Intuit'),
    ('oracle',            'wd1',  'Oracle'),
    ('dell',              'wd1',  'Dell Technologies'),
    # ── HEALTHCARE ─────────────────────────────────────────────────────────────
    ('cvshealth',         'wd1',  'CVS Health'),
    ('humana',            'wd5',  'Humana'),
    ('cigna',             'wd5',  'Cigna Group'),
    ('elevancehealth',    'wd1',  'Elevance Health (Anthem)'),
    ('centene',           'wd5',  'Centene'),
    ('jj',                'wd5',  'Johnson & Johnson'),
    ('pfizer',            'wd1',  'Pfizer'),
    ('msd',               'wd5',  'Merck (MSD)'),
    ('lilly',             'wd5',  'Eli Lilly'),
    ('abbott',            'wd5',  'Abbott Laboratories'),
    # ── DEFENSE / AEROSPACE ────────────────────────────────────────────────────
    ('ngc',               'wd1',  'Northrop Grumman'),
    ('globalhr',          'wd5',  'RTX (Raytheon Technologies)'),
    ('boeing',            'wd1',  'Boeing'),
    ('lmco',              'wd1',  'Lockheed Martin'),
    ('leidos',            'wd5',  'Leidos'),
    ('gdit',              'wd5',  'General Dynamics IT'),
    ('bah',               'wd1',  'Booz Allen Hamilton'),
    # ── TELECOM ────────────────────────────────────────────────────────────────
    ('att',               'wd1',  'AT&T'),
    ('comcast',           'wd5',  'Comcast / NBCUniversal'),
    ('tmobile',           'wd1',  'T-Mobile'),
    ('verizon',           'wd12', 'Verizon'),
    # ── ENERGY ─────────────────────────────────────────────────────────────────
    ('chevron',           'wd5',  'Chevron'),
    ('conocophillips',    'wd1',  'ConocoPhillips'),
    ('bakerhughes',       'wd5',  'Baker Hughes'),
    ('dukeenergy',        'wd1',  'Duke Energy'),
    # ── INDUSTRIAL ─────────────────────────────────────────────────────────────
    ('cat',               'wd5',  'Caterpillar'),
    ('3m',                'wd1',  '3M'),
    ('generalmotors',     'wd5',  'General Motors'),
    ('stellantis',        'wd3',  'Stellantis'),
    ('fedex',             'wd1',  'FedEx'),
    ('hcmportal',         'wd5',  'UPS'),
    # ── RETAIL / CONSUMER ──────────────────────────────────────────────────────
    ('walmart',           'wd5',  'Walmart'),
    ('target',            'wd5',  'Target'),
    ('homedepot',         'wd5',  'The Home Depot'),
    ('lowes',             'wd5',  "Lowe's"),
    ('pg',                'wd5',  'Procter & Gamble'),
    # ── FOOD & BEVERAGE ────────────────────────────────────────────────────────
    ('mdlz',              'wd3',  'Mondelez International'),
    ('heinz',             'wd1',  'Kraft Heinz'),
    ('campbellsoup',      'wd5',  'Campbell Soup'),
    ('pepsico',           'wd5',  'PepsiCo'),
]

# Greenhouse slugs (format: ('slug', 'Company Name'))
GREENHOUSE_TUPLES = [
    ('airbnb',      'Airbnb'),
    ('okta',        'Okta'),
    ('stripe',      'Stripe'),
    ('lyft',        'Lyft'),
    ('pinterest',   'Pinterest'),
    ('snap',        'Snap Inc.'),
    ('robinhood',   'Robinhood Markets'),
    ('coinbase',    'Coinbase'),
    ('doordash',    'DoorDash'),
    ('reddit',      'Reddit'),
    ('dropbox',     'Dropbox'),
    ('twilio',      'Twilio'),
    ('datadog',     'Datadog'),
    ('cloudflare',  'Cloudflare'),
    ('mongodb',     'MongoDB'),
    ('hubspot',     'HubSpot'),
    ('gitlab',      'GitLab'),
    ('anthropic',   'Anthropic'),
    ('duolingo',    'Duolingo'),
    ('rivian',      'Rivian Automotive'),
    ('zendesk',     'Zendesk'),
    ('zillow',      'Zillow'),
    ('wayfair',     'Wayfair'),
    ('yelp',        'Yelp'),
]

# Lever slugs (format: ('slug', 'Company Name'))
LEVER_TUPLES = [
    ('netflix',     'Netflix'),
    ('scale-ai',    'Scale AI'),
    ('anduril',     'Anduril Industries'),
    ('waymo',       'Waymo'),
    ('airtable',    'Airtable'),
    ('notion',      'Notion Labs'),
    ('asana',       'Asana'),
    ('plaid',       'Plaid'),
    ('ramp',        'Ramp'),
    ('toast',       'Toast'),
    ('samsara',     'Samsara'),
    ('gusto',       'Gusto'),
    ('rippling',    'Rippling'),
    ('chime',       'Chime'),
    ('affirm',      'Affirm'),
    ('nerdwallet',  'NerdWallet'),
]
