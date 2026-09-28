# Primary-source ledger, 28 September 2026

Own-source watchers loaded through `config/sources/primary.yaml`: 265 pages and 17 feeds across 223 names. Tier A: 68 of 75 names have at least one own source (the rest are listed with the reason). Comps: 161 of 230.

**Loaded** means the page was fetched in this sweep and shown to list article links, and the link pattern was tested against real links from it. **Candidate** means the source exists but could not be confirmed as a fetchable link list (JavaScript shell, login portal, robots or WAF block, or fetched without any article link visible); it sits in `primary_candidates.yaml`, which the engine does not load, until `scripts/check_primary.py --candidates` shows links on it.

## Tier A

| id | name | loaded | candidates (not loaded) |
|---|---|---|---|
| adler | Adler Group | newsroom, investor page | venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| advanz | Advanz Pharma | newsroom | investor page: gated |
| airbaltic | airBaltic | newsroom, investor page | venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| arrow | Arrow Global | newsroom, investor page |  |
| avis | Avis Budget Group | newsroom, RSS | investor page: unverified |
| axactor | Axactor | newsroom, investor page | venue: JS-rendered or not verified as a link list |
| b2impact | B2 Impact | newsroom, investor page | venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| bertrand | Groupe Bertrand | newsroom |  |
| biogroup | Biogroup | investor news (2 watchers in the name file) |  |
| boels | Boels Rental | newsroom, investor page |  |
| boots | Boots UK | none | newsroom: article list renders client-side; hrefs seen only via search results |
| branicks | Branicks Group | newsroom | investor page: unverified |
| ceconomy | Ceconomy | newsroom, regulatory news (EQS) |  |
| centrient | Centrient Pharmaceuticals | newsroom | investor page: unverified |
| cerba | Cerba HealthCare | newsroom, investor page |  |
| cheplapharm | Cheplapharm | investor page |  |
| cmacgm | CMA CGM | newsroom, RSS | investor page: gated |
| cnam | CNAM (French lab tariffs) | newsroom; Assurance Maladie releases (name file) |  |
| coop | Co-op (Co-operative Group) | newsroom, investor page, Co-op RNS (Investegate) |  |
| demire | DEMIRE | newsroom, investor page |  |
| dovalue | doValue | newsroom, investor page |  |
| elior | Elior | newsroom | investor page: unverified; venue: JS-rendered or not verified as a link list |
| europcar | Europcar | newsroom | investor page: gated |
| evoca | Evoca Group | newsroom, investor page |  |
| flora | Flora Food Group | none | newsroom: /stories is a JS app; sample links came from search; investor page: gated |
| flos | Design Holding (Flos B&B Italia) | newsroom, investor page |  |
| fressnapf | Fressnapf | newsroom, regulatory news (EQS) | investor page: gated |
| froneri | Froneri | newsroom, investor page |  |
| goldengoose | Golden Goose | newsroom |  |
| grifols | Grifols | newsroom, investor page | venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| grunenthal | Grünenthal | newsroom, Grunenthal regulatory news (EQS) |  |
| hapag | Hapag-Lloyd | newsroom |  |
| heimstaden | Heimstaden | newsroom | investor page: unverified |
| hellofresh | HelloFresh | newsroom, investor page, regulatory news (EQS) |  |
| househr | House of HR | newsroom | investor page: gated; venue: JS-rendered or not verified as a link list |
| hse | HSE (Home Shopping Europe) | Presseportal releases (name file) | newsroom: Investor content behind registration. |
| intrum | Intrum AB | MFN feed + 15 country newsrooms + 5 tag pages (name file) | newsroom: intrum.com press page is JS-rendered; MFN watcher already in the name file; venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| iqera | iQera | communiqués (name file) | newsroom: iqera.it is a different entity; list page JS-rendered; investor area login.; investor page: gated |
| isabelmarant | Isabel Marant | none | newsroom: Only page found is a fully login-gated investor portal (issuer IM Group SAS); no public pr |
| keepmoat | Keepmoat | newsroom |  |
| kiloutou | Kiloutou | newsroom | investor page: gated |
| lowell | Lowell (Garfunkelux) | newsroom |  |
| loxam | Loxam | newsroom | investor page: unverified; venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| manuchar | Manuchar | news (name file) | investor page: gated |
| maxeda | Maxeda DIY Group | newsroom |  |
| merlin | Merlin Entertainments | newsroom | investor page: gated |
| metlen | Metlen Energy & Metals | Metlen press releases, Metlen regulatory announcements |  |
| mobilux | BUT / Mobilux | Autorité de la concurrence (name file) | newsroom: Two candidate IR domains found for issuer Mobilux 2 SAS / Mobilux Finance SAS: but-financi |
| motorfuel | Motor Fuel Group | newsroom, investor page |  |
| mutares | Mutares | newsroom, regulatory news (EQS) | investor page: gated |
| paragon | Paragon Group (Customer Communications) | newsroom | investor page: unverified |
| pizzaexpress | PizzaExpress | newsroom | investor page: gated; venue: JS-rendered or not verified as a link list |
| pra | PRA Group | newsroom, RSS |  |
| puregym | PureGym | newsroom, investor page |  |
| quick | Quick (France) | none |  |
| rekeep | Rekeep | newsroom, investor page | venue: list renders client-side (LuxSE, Euronext, TISE, Epiq, EDGAR, CNMV) |
| rossini | Rossini (Recordati) | none | newsroom: recordati.com returned 403 on every path (WAF); URLs unverified.; investor page: unverified |
| sbb | SBB (Samhällsbyggnadsbolaget) | newsroom, SBB regulatory feed (MFN) |  |
| skechers | Skechers | newsroom | venue: JS-rendered or not verified as a link list |
| stada | STADA | investor page, regulatory news (EQS) | newsroom: press-releases hub is JS-rendered; EQS page carries the regulatory news |
| sudzucker | Südzucker | newsroom |  |
| synlab | SYNLAB | regulatory news (EQS) | newsroom: Both synlab.com and synlab.ag block fetch via robots.txt; EQS pages also exist for Synlab ; investor page: unverified |
| takko | Takko Fashion | newsroom | investor page: unverified |
| tereos | Tereos | newsroom, investor page | venue: JS-rendered or not verified as a link list |
| teva | Teva | newsroom, RSS |  |
| thom | Thom Group | newsroom, investor page |  |
| tmicc | The Magnum Ice Cream Company | newsroom, investor page | venue: JS-rendered or not verified as a link list |
| travelodge | Travelodge | newsroom, investor page | venue: JS-rendered or not verified as a link list |
| tuicruises | TUI Cruises | none | newsroom: tuicruises.com/presse redirects to meinschiff.com/presse, JS-rendered; no bondholder page  |
| tuigroup | TUI Group | investor page, TUI AG regulatory news (EQS) | newsroom: newsroom is a JS app returning an empty shell; EQS page carries the news |
| versuni | Versuni | newsroom |  |
| very | The Very Group | newsroom, investor page | venue: JS-rendered or not verified as a link list |
| vivion | Vivion Investments | none | newsroom: vivion.lu and ir.vivion.com do not resolve; vivion.com thin; domain to confirm. |
| wagamama | Wagamama | newsroom |  |
| worldline | Worldline | newsroom | venue: JS-rendered or not verified as a link list |

## Comps

| id | name | loaded | candidates (not loaded) |
|---|---|---|---|
| 123tv | 1-2-3.tv | none |  |
| abfoods | Associated British Foods | RNS (Investegate) | newsroom: JS-rendered hub; sample link came from search |
| acsdobfar | ACS Dobfar | newsroom |  |
| action | Action | none |  |
| adecco | Adecco | newsroom |  |
| adidas | Adidas | newsroom | investor page: not fetched in the comp sweep |
| adyen | Adyen | none | newsroom: JS SPA.; investor page: not fetched in the comp sweep |
| airfranceklm | Air France-KLM | newsroom | investor page: not fetched in the comp sweep |
| alten | Alten | newsroom | investor page: not fetched in the comp sweep |
| alteri | Alteri Investors | none |  |
| amazon | Amazon | newsroom | investor page: not fetched in the comp sweep |
| amrest | AmRest | newsroom | investor page: not fetched in the comp sweep |
| anglikang | Anglikang | none |  |
| aoworld | AO World | none |  |
| applegreen | Applegreen | none | newsroom: JS-rendered. |
| aramark | Aramark | newsroom | investor page: not fetched in the comp sweep |
| arcaplanet | Arcaplanet | newsroom |  |
| arla | Arla Foods | newsroom |  |
| aroundtown | Aroundtown | newsroom |  |
| asda | Asda | none | newsroom: JS SPA. |
| ashtead | Ashtead Group | newsroom | investor page: not fetched in the comp sweep |
| aspen | Aspen Pharmacare | newsroom |  |
| aurelius | Aurelius Group | none | newsroom: JS-rendered list; sample link came from search |
| aurobindo | Aurobindo Pharma | none | newsroom: JS-rendered.; investor page: not fetched in the comp sweep |
| azelis | Azelis | none | newsroom: Timeouts; hrefs not exposed. |
| balder | Fastighets AB Balder | newsroom |  |
| barrattredrow | Barratt Redrow | newsroom | investor page: not fetched in the comp sweep |
| basicfit | Basic-Fit | none | newsroom: JS-rendered list; document links came from search |
| bayer | Bayer | newsroom | investor page: not fetched in the comp sweep |
| bellway | Bellway | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| bianchivending | Bianchi Industry | newsroom |  |
| birkenstock | Birkenstock | newsroom |  |
| blueapron | Blue Apron | none |  |
| boohoo | boohoo | RNS (Investegate) | newsroom: Renamed Debenhams Group; JS links.; investor page: not fetched in the comp sweep |
| booking | Booking Holdings | RSS | newsroom: JS shell.; investor page: not fetched in the comp sweep |
| bosch | Bosch | none | newsroom: Group press portal, JS-rendered. |
| brenntag | Brenntag | none | newsroom: JS-rendered.; investor page: not fetched in the comp sweep |
| bricodepot | Brico Dépôt | none | newsroom: parent Kingfisher page (JS hub); no standalone newsroom |
| britishsugar | British Sugar | newsroom |  |
| ca | C&A | newsroom |  |
| caimmo | CA Immo | newsroom | investor page: not fetched in the comp sweep |
| campbells | Campbell's | newsroom | investor page: not fetched in the comp sweep |
| carnival | Carnival Corporation | none | newsroom: Third-party IR API. |
| castellum | Castellum | regulatory feed (MFN) | newsroom: JS-loaded list.; investor page: not fetched in the comp sweep |
| centrakor | Centrakor | none | newsroom: Hosted press room. |
| channel21 | Channel 21 | none |  |
| christ | CHRIST (jeweller) | newsroom |  |
| chuanning | Chuanning Biotech | none |  |
| circlek | Circle K | newsroom, RSS | investor page: not fetched in the comp sweep |
| comcast | Comcast | none | newsroom: Dynamic list. |
| compagniedesalpes | Compagnie des Alpes | newsroom |  |
| compass | Compass Group | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| conforama | Conforama | none |  |
| cosco | COSCO Shipping | none |  |
| covis | Covis Pharma | newsroom |  |
| cpiproperty | CPI Property Group | none | newsroom: JS-rendered list.; investor page: not fetched in the comp sweep |
| cranems | Crane (vending) | newsroom |  |
| cristalunion | Cristal Union | none | newsroom: JS-rendered. |
| crocs | Crocs | RSS | newsroom: RSS confirmed. |
| cslbehring | CSL Behring | newsroom |  |
| cspc | CSPC Pharmaceutical | newsroom | investor page: not fetched in the comp sweep |
| currys | Currys | newsroom, RNS (Investegate) |  |
| danone | Danone | newsroom |  |
| davidlloyd | David Lloyd | newsroom |  |
| deckers | Deckers | newsroom | investor page: not fetched in the comp sweep |
| delonghi | De'Longhi | newsroom | investor page: not fetched in the comp sweep |
| dertouristik | DER Touristik | newsroom |  |
| disneyparks | Disney Parks | newsroom, RSS |  |
| dojo | Dojo (payments) | none |  |
| dominosuk | Domino's Pizza Group | RNS (Investegate) |  |
| drmartens | Dr. Martens | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| drreddys | Dr. Reddy's | none | newsroom: JS-driven links. |
| dyson | Dyson | none | newsroom: Mixed content hub. |
| easyhotel | easyHotel | none | newsroom: Hrefs not extractable.; investor page: not fetched in the comp sweep |
| easyjetholidays | easyJet holidays | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| eggroup | EG Group | newsroom | investor page: not fetched in the comp sweep |
| encore | Encore Capital Group | newsroom |  |
| endo | Endo International | none | newsroom: JS-driven; merging with Mallinckrodt. |
| enterprise | Enterprise Holdings | newsroom |  |
| equiniti | Equiniti | newsroom |  |
| eurofins | Eurofins Scientific | none | newsroom: JS-rendered list. |
| eversys | Eversys | newsroom |  |
| expedia | Expedia | RSS | newsroom: JS-rendered. |
| finnair | Finnair | none | newsroom: JS-rendered.; investor page: not fetched in the comp sweep |
| fiserv | Fiserv | newsroom | investor page: not fetched in the comp sweep |
| fisworldpay | FIS / Worldpay | newsroom |  |
| fitnessfirst | Fitness First | newsroom |  |
| fnacdarty | Fnac Darty | newsroom |  |
| frieslandcampina | FrieslandCampina | none |  |
| ganni | GANNI | none |  |
| gcity | G City Europe | none | newsroom: Press page fetch timed out; not g-city.com.; investor page: not fetched in the comp sweep |
| gifi | Gifi | none | newsroom: Login required. |
| gousto | Gousto | none |  |
| grandcity | Grand City Properties | newsroom |  |
| greggs | Greggs | newsroom, RNS (Investegate) |  |
| groupeseb | Groupe SEB | newsroom | investor page: not fetched in the comp sweep |
| gsk | GSK | newsroom, RNS (Investegate) |  |
| gymgroup | The Gym Group | newsroom, RNS (Investegate) |  |
| hamborner | Hamborner REIT | none | newsroom: JS-loaded. |
| haworth | Haworth | newsroom |  |
| hays | Hays | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| hertz | Hertz | RSS | newsroom: Hrefs not extractable.; investor page: not fetched in the comp sweep |
| hhglobal | HH Global | newsroom |  |
| hikma | Hikma Pharmaceuticals | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| hoist | Hoist Finance | newsroom, regulatory feed (MFN) |  |
| hornbach | Hornbach | newsroom | investor page: not fetched in the comp sweep |
| ibisbudget | ibis budget | none | newsroom: Accor group pressroom, JS. |
| icelandfoods | Iceland Foods | none |  |
| imcd | IMCD | none |  |
| ingenico | Ingenico | newsroom |  |
| inovie | Inovie | newsroom |  |
| interikea | Inter IKEA Group | newsroom |  |
| iss | ISS A/S | newsroom |  |
| jet2 | Jet2 | newsroom | investor page: not fetched in the comp sweep |
| kedrion | Kedrion | newsroom |  |
| kik | KiK | newsroom |  |
| kingfisher | Kingfisher | RNS (Investegate) | newsroom: JS hub; sample link came from search |
| labcorp | Labcorp | newsroom |  |
| lactalis | Lactalis | newsroom |  |
| leduff | Groupe Le Duff | none |  |
| leg | LEG Immobilien | newsroom | investor page: not fetched in the comp sweep |
| leroymerlin | Leroy Merlin | newsroom |  |
| lufthansa | Lufthansa | newsroom, RSS |  |
| lupin | Lupin | newsroom | investor page: not fetched in the comp sweep |
| lyfius | Lyfius | none |  |
| maersk | Maersk | newsroom |  |
| maisonsdumonde | Maisons du Monde | newsroom |  |
| maje | Maje (SMCP) | newsroom |  |
| marleyspoon | Marley Spoon | newsroom |  |
| mcdonaldsfrance | McDonald's France | newsroom |  |
| millerknoll | MillerKnoll | newsroom |  |
| mollie | Mollie | newsroom |  |
| morellato | Morellato | newsroom |  |
| morrisons | Morrisons | newsroom |  |
| motelone | Motel One | none |  |
| mrbricolage | Mr Bricolage | newsroom |  |
| msc | MSC (Mediterranean Shipping Company) | newsroom |  |
| msccruises | MSC Cruises | newsroom |  |
| nandos | Nando's | none |  |
| napaqaro | Napaqaro | none |  |
| natuzzi | Natuzzi | newsroom | investor page: not fetched in the comp sweep |
| nbrown | N Brown | newsroom |  |
| ncl | Norwegian Cruise Line | newsroom |  |
| ncpc | NCPC (North China Pharmaceutical) | none |  |
| nexi | Nexi | newsroom |  |
| next | Next plc | RNS (Investegate) | newsroom: Investis iframe. |
| nike | Nike | newsroom |  |
| nkd | NKD | newsroom |  |
| nordzucker | Nordzucker | newsroom |  |
| nuffield | Nuffield Health | newsroom |  |
| octapharma | Octapharma | none | newsroom: Client-side list. |
| onholding | On Holding | newsroom |  |
| opengate | OpenGate Capital | newsroom |  |
| organon | Organon | none | newsroom: JS-loaded.; investor page: not fetched in the comp sweep |
| otto | Otto Group | newsroom |  |
| pandora | Pandora | newsroom | investor page: not fetched in the comp sweep |
| parkland | Parkland | none | newsroom: Hrefs not extractable. |
| parquesreunidos | Parques Reunidos | none |  |
| paypal | PayPal | newsroom | investor page: not fetched in the comp sweep |
| peachproperty | Peach Property Group | newsroom |  |
| pepco | Pepco Group | newsroom |  |
| persimmon | Persimmon | newsroom, RNS (Investegate) |  |
| petsathome | Pets at Home | RNS (Investegate) | newsroom: Euroland widget. |
| pfizer | Pfizer | newsroom | investor page: not fetched in the comp sweep |
| pho | Pho (restaurant group) | none |  |
| planetfitness | Planet Fitness | RSS |  |
| premierinn | Premier Inn | newsroom, RNS (Investegate) |  |
| primark | Primark | newsroom |  |
| puma | Puma | newsroom | investor page: not fetched in the comp sweep |
| quadient | Quadient | newsroom |  |
| quest | Quest Diagnostics | newsroom | investor page: not fetched in the comp sweep |
| quironsalud | Helios Spain (Quirónsalud) | newsroom |  |
| qvc | QVC | newsroom |  |
| ragbone | rag & bone | none |  |
| raizen | Raízen | newsroom |  |
| randstad | Randstad | newsroom |  |
| restaurantgroup | The Restaurant Group | none |  |
| rheavendors | Rheavendors | newsroom |  |
| ribera | Ribera Salud | newsroom |  |
| roberthalf | Robert Half | newsroom |  |
| rosasthai | Rosa's Thai | none |  |
| royalcaribbean | Royal Caribbean | newsroom |  |
| ryanair | Ryanair | none | newsroom: Bot-protected, JS. |
| sainsburys | Sainsbury's | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| sandoz | Sandoz | none | newsroom: JS-rendered. |
| sandro | Sandro (SMCP) | newsroom |  |
| savencia | Savencia | newsroom |  |
| sielaff | Sielaff | newsroom |  |
| signet | Signet Jewelers | RSS | newsroom: JS-loaded.; investor page: not fetched in the comp sweep |
| sixt | Sixt | newsroom |  |
| sodexo | Sodexo | newsroom |  |
| sonic | Sonic Healthcare | newsroom | investor page: not fetched in the comp sweep |
| sonnenklar | Sonnenklar TV | none |  |
| ssp | SSP Group | RNS (Investegate) |  |
| steelcase | Steelcase | newsroom |  |
| stripe | Stripe | newsroom |  |
| sumup | SumUp | newsroom |  |
| superdrug | Superdrug | none |  |
| swarovski | Swarovski | none |  |
| tagimmobilien | TAG Immobilien | newsroom, ad-hoc announcements | investor page: not fetched in the comp sweep |
| takeda | Takeda | none | newsroom: JS-rendered list. |
| taylorwimpey | Taylor Wimpey | newsroom | investor page: not fetched in the comp sweep |
| tesco | Tesco | RNS (Investegate) |  |
| teya | Teya | none |  |
| thermoplan | Thermoplan | newsroom |  |
| thirdspace | Third Space | none |  |
| thomascook | Thomas Cook | newsroom |  |
| triton | Triton Partners | newsroom |  |
| tul | The United Laboratories | newsroom | investor page: not fetched in the comp sweep |
| unilabs | Unilabs | none |  |
| unilever | Unilever | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| unitedrentals | United Rentals | RSS | newsroom: JS list. |
| univar | Univar Solutions | RSS | newsroom: RSS advertised. |
| vendo | SandenVendo | newsroom |  |
| viatris | Viatris | newsroom |  |
| virginactive | Virgin Active | none |  |
| vistry | Vistry Group | newsroom, RNS (Investegate), RSS |  |
| vivacom | Viva.com | none |  |
| vonovia | Vonovia | newsroom | investor page: not fetched in the comp sweep |
| walgreens | Walgreens Boots Alliance | none | newsroom: JS-rendered. |
| watchesofswitzerland | Watches of Switzerland | RNS (Investegate) |  |
| weiqida | Sinopharm Weiqida | none |  |
| wickes | Wickes | newsroom, RNS (Investegate) | investor page: not fetched in the comp sweep |
| williamslea | Williams Lea | newsroom |  |
| wizzair | Wizz Air | RNS (Investegate) | newsroom: Paginated JS widget.; investor page: not fetched in the comp sweep |
| wmf | WMF | newsroom |  |
| xerox | Xerox | newsroom |  |
| zadigvoltaire | Zadig & Voltaire | none |  |
| zim | ZIM | RSS | newsroom: JS-loaded. |
| zooplus | Zooplus | newsroom |  |
