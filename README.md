# Diversitas Sestava

Kosarica sestih kripto sredstev, kjer vsako trguje po svojem signalu, primerjana
proti samemu BTC.

Privzeta razporeditev je BTC 50 % ter ETH, SOL, LINK, BNB in sesto mesto po
10 %. Utezi se dajo na strani spremeniti.

Strategija na posameznem sredstvu je ista kot v projektu **Diversitas Lean**.
Ta stran ne spreminja signala, ampak odgovarja na drugo vprasanje: ali se
splaca razporediti po vec sredstvih, ali je bolje ostati pri BTC.

## Zagon

Potrebuje **Python 3.11 ali novejsi**.

```
pip install -r requirements.txt

python scripts/preveri_vire.py     preveri knjiznice in dosegljivost virov cen
python scripts/preveri.py          preveri, da racun in izris drzita
streamlit run app.py               odpre stran
```

`preveri.py` pozene tudi celo stran brezglavo. To ujame napake, ki jih racun
sam ne more: manjkajoco knjiznico za izris, napacen tip stolpca, zlom v Plotly.
Ce ta preverba pade, stran v brskalniku ne bo delovala.

## Od kod pridejo cene

Vsako sredstvo ima **tocno en vir in nadomestnega ni**. Nadomestni vir bi tiho
spremenil vsako izracunano stevilko, zato stran raje pove, da nima podatkov.

| sredstvo | vir | zakaj ta |
|---|---|---|
| BTC, ETH, SOL, LINK | Coinbase | globoka zgodovina, isti vir kot lean |
| BNB | Yahoo | Coinbase ga ne kotira pred oktobrom 2025 |
| XRP | Yahoo | pri Coinbaseu je bil med 2021 in 2023 umaknjen |
| HYPE | Hyperliquid | drugje ga ni |
| SPY | Yahoo | za primerjavo z delnicami |

### yfinance ni izbiren

Naveden je v `requirements.txt` in mora tam ostati. Koda za Yahoo najprej
poskusi navaden HTTP klic prek `requests`, ta pa danes ne dela vec, zato pade
na `yfinance`.

Preveril sem tako, da sem uvoz `yfinance` umetno blokiral:

```
BNB   PADE: source 'yahoo' failed and strict=True
XRP   PADE: source 'yahoo' failed and strict=True
SPY   PADE: source 'yahoo' failed and strict=True
BTC   OK  4062 vrstic
```

Tri od osmih sredstev. Zato `scripts/preveri_vire.py` preveri tudi knjiznice,
ne le dosegljivost borz.

`pyarrow` prav tako ni nikjer uvozen, rabi pa ga Streamlit za izris tabel. Ne
odstranjuj ga.

### Zakaj Binancea ni

Raziskava, iz katere je ta stran nastala, je BNB in XRP jemala neposredno z
Binancea. Tu ga ni, ker iz Streamlit Clouda vrne `HTTP 451 Service unavailable
from a restricted location`. Zavraca IP naslove podatkovnih centrov, isti klic
z domacega racunalnika pa vrne 200. To ni izpad in ne bo minilo.

Razlika je izmerjena, ne ocenjena. Na BNB, 3217 skupnih dni od novembra 2017:

| | Sharpe | Sortino | letno | najhujsi padec |
|---|---|---|---|---|
| Binance | 0,83 | 1,25 | 31 % | -59 % |
| Yahoo | 0,81 | 1,22 | 30 % | -58 % |

Povprecna razlika cen je 0,063 %, mediana 0,018 %, in **pozicija se ujema na
99,0 % dni**. Razlika je torej majhna, ni pa nicelna. Ce primerjas stevilke s
starejsimi porocili iz raziskave, je to razlog za manjse odstopanje.

## Kaj stran pokaze

Zgoraj so nastavitve: datum vstopa in izstopa, utezi, provizija in zdrs,
pogostost uravnavanja, ter kdo zasede sesto mesto.

Pod njimi je glavna tabela s primerjavo sestave proti sami BTC strategiji,
proti obema kupi in drzi, ter proti S&P 500.

Nato pet zavihkov:

**Krivulja in padci** Vrednost skozi cas, graf pod vodo, in seznam najhujsih
padcev z datumom vrha, dna, okrevanja in trajanjem.

**Skozi cas** Kotaleci se Sharpe in donos po letih.

**Sredstva** Kaj prispeva vsako sredstvo posebej.

**Obcutljivost na vstop** Isti izracun pri razlicnih datumih vstopa. To je
najbolj posten pogled, ker pove, ali je rezultat odvisen od enega samega
srecnega zacetka.

**Stroski** Kam gre 100 vlozenih enot, obcutljivost na visino provizije, in
razclenitev po fazah cikla.

### Faze cikla

Faze so datirane po algoritmu Bry in Boschan v razlicici Pagan in Sossounov, ki
je akademski standard. Poisce lokalne vrhove in dna v oknu 90 dni, vsili
izmenjavanje vrh in dno, ter odvrze faze, krajse od 120 dni ali manjse od 25 %.

Meja torej ni rocno izbrana. To je pomembno, ker preprosta meja tipa cena nad
200-dnevnim povprecjem niha sem in tja in steje kratek prehod enako kot
dvoletni medvedji trg.

## Kako se racuna

Racun vodi **denar po nalozbah**, ne donosov. Vsaka nalozba je znesek, ki raste
in se manjsa, provizije se odbijejo od zneska, metrike pa se izracunajo sele iz
poti skupne vrednosti.

To ni pedantnost. Prejsnja razlicica je strosek uravnavanja odstela od donosa,
ne pa tudi od nalozbe, in je zato dala nekoliko previsoke stevilke.

Provizije so razclenjene na tri dele:

**signali** so vstopi in izstopi strategije po vsakem sredstvu posebej.

**uravnavanje** je vracanje na ciljne utezi. Zaracuna se samo tisto, kar se
dejansko premakne: ce BTC zdrsne s 50 % na 53 %, placas od tistih treh
odstotnih tock, ne od celega portfelja.

**zamenjava** je enkratna menjava sestega mesta, kadar HYPE dobi prvi signal.

## Kaj je 0,30 % na stran

Privzeta provizija in zdrs skupaj. **To je predpostavka, ne meritev**, in edini
vhod v celoten izracun, ki ga ne poznamo.

Zato je v zavihku Stroski tabela, ki pokaze, koliko se izid premakne med
brezplacnim trgovanjem in 0,60 % na stran. Manjsa ko je ta razlika, manj je
rezultat odvisen od necesa, cesar ne vemo.

## Objava na Streamlit Cloud

Glavna datoteka je `app.py` v korenu.

V naprednih nastavitvah **izberi Python 3.11 ali novejsi**. Na starejsem
namestitev pandasa pade z napako, ki ne kaze ocitno na verzijo.

Skrivnosti niso potrebne. Cene se predpomnijo za eno uro.

Prvo nalaganje traja dlje kot pri lean strani, ker prenese cene za osem
sredstev in na vsakem pozene strategijo.

Ce stran javi, da nima podatkov, pozeni `python scripts/preveri_vire.py`.

## Postavitev

```
app.py                  streamlit run app.py

model/                  strategija, ista kot v Diversitas Lean
  config.py             vsi parametri, z razlogom za vsakega
  strategy.py           avtomat stanj, pogoji, obrezovanje ogrevanja
  indicators.py         RSI, drseca povprecja, najvisje in najnizje
  warmup.py             obrezovanje ogrevanja
  costs.py              model stroskov
  data_source.py        pridobivanje cen

ui/
  dashboard.py          stran

scripts/
  preveri.py            racun in izris
  preveri_vire.py       knjiznice in dosegljivost virov cen
```

Mapa je zgrajena iz raziskovalnega repozitorija s skriptom
`testing/scripts/zgradi_sestavo.py`. Ta jo zna zgraditi znova, kadar se v
izvirniku kaj spremeni, in pri tem pobrise vse razen `.git`. **Rocne spremembe v
tej mapi bo prepisal**, zato popravljaj izvirnik.

## Kaj ta stran ni

Ni priporocilo za razporeditev. Raziskava, iz katere je nastala, je pokazala,
da kosarica sestih **ne** premaga samega BTC prepricljivo, in da razprsitev med
kripto sredstvi prinese manj, kot bi clovek pricakoval. Povprecna korelacija med
temi sestimi je 0,73, kar pomeni, da se vedejo skoraj kot eno sredstvo.

Stran obstaja zato, da to lahko vidis sam, na svojih datumih in svojih utezeh.
