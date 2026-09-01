# Diversitas Sestava

Košarica šestih kripto sredstev, kjer vsako trguje po svojem signalu, primerjana
proti samemu BTC.

Privzeta razporeditev je BTC 50 % ter ETH, SOL, LINK, BNB in šesto mesto po
10 %. Uteži se dajo na strani spremeniti.

Strategija na posameznem sredstvu je ista kot v projektu **Diversitas Lean**.
Ta stran ne spreminja signala, ampak odgovarja na drugo vprašanje: ali se
splača razporediti po več sredstvih, ali je bolje ostati pri BTC.

## Zagon

Potrebuje **Python 3.11 ali novejši**.

```
pip install -r requirements.txt

python scripts/preveri_vire.py     preveri, ali so vsi viri cen dosegljivi
python scripts/preveri.py          preveri, da račun in izris držita
streamlit run app.py               odpre stran
```

`preveri.py` požene tudi celo stran brezglavo. To ujame napake, ki jih račun
sam ne more: manjkajočo knjižnico za izris, napačen tip stolpca, zlom v Plotly.
Če ta preverba pade, stran v brskalniku ne bo delovala.

## Od kod pridejo cene

Vsako sredstvo ima **točno en vir in nadomestnega ni**. Nadomestni vir bi tiho
spremenil vsako izračunano številko, zato stran raje pove, da nima podatkov.

| sredstvo | vir | zakaj ta |
|---|---|---|
| BTC, ETH, SOL, LINK | Coinbase | globoka zgodovina, isti vir kot lean |
| BNB | Yahoo | Coinbase ga ne kotira pred oktobrom 2025 |
| XRP | Yahoo | pri Coinbaseu je bil med 2021 in 2023 umaknjen |
| HYPE | Hyperliquid | drugje ga ni |
| SPY | Yahoo | za primerjavo z delnicami |

### Zakaj Binancea ni

Raziskava, iz katere je ta stran nastala, je BNB in XRP jemala neposredno z
Binancea. Tu ga ni, ker iz Streamlit Clouda vrne `HTTP 451 Service unavailable
from a restricted location`. Zavrača IP naslove podatkovnih centrov, isti klic
z domačega računalnika pa vrne 200. To ni izpad in ne bo minilo.

Razlika je izmerjena, ne ocenjena. Na BNB, 3217 skupnih dni od novembra 2017:

| | Sharpe | Sortino | letno | najhujši padec |
|---|---|---|---|---|
| Binance | 0,83 | 1,25 | 31 % | −59 % |
| Yahoo | 0,81 | 1,22 | 30 % | −58 % |

Povprečna razlika cen je 0,063 %, mediana 0,018 %, in **pozicija se ujema na
99,0 % dni**. Razlika je torej majhna, ni pa ničelna. Če primerjaš številke s
starejšimi poročili iz raziskave, je to razlog za manjše odstopanje.

## Kaj stran pokaže

Zgoraj so nastavitve: datum vstopa in izstopa, uteži, provizija in zdrs,
pogostost uravnavanja, ter kdo zasede šesto mesto.

Pod njimi je glavna tabela s primerjavo sestave proti sami BTC strategiji,
proti obema kupi in drži, ter proti S&P 500.

Nato pet zavihkov:

**Krivulja in padci** Vrednost skozi čas, graf pod vodo, in seznam najhujših
padcev z datumom vrha, dna, okrevanja in trajanjem.

**Skozi čas** Kotaleči se Sharpe in donos po letih.

**Sredstva** Kaj prispeva vsako sredstvo posebej.

**Občutljivost na vstop** Isti izračun pri različnih datumih vstopa. To je
najbolj pošten pogled, ker pove, ali je rezultat odvisen od enega samega
srečnega začetka.

**Stroški** Kam gre 100 vloženih enot, občutljivost na višino provizije, in
razčlenitev po fazah cikla.

### Faze cikla

Faze so datirane po algoritmu Bry in Boschan v različici Pagan in Sossounov, ki
je akademski standard. Poišče lokalne vrhove in dna v oknu 90 dni, vsili
izmenjavanje vrh in dno, ter odvrže faze, krajše od 120 dni ali manjše od 25 %.

Meja torej ni ročno izbrana. To je pomembno, ker preprosta meja tipa cena nad
200-dnevnim povprečjem niha sem in tja in šteje kratek prehod enako kot
dvoletni medvedji trg.

## Kako se računa

Račun vodi **denar po naložbah**, ne donosov. Vsaka naložba je znesek, ki raste
in se manjša, provizije se odbijejo od zneska, metrike pa se izračunajo šele iz
poti skupne vrednosti.

To ni pedantnost. Prejšnja različica je strošek uravnavanja odštela od donosa,
ne pa tudi od naložbe, in je zato dala nekoliko previsoke številke.

Provizije so razčlenjene na tri dele:

**signali** so vstopi in izstopi strategije po vsakem sredstvu posebej.

**uravnavanje** je vračanje na ciljne uteži. Zaračuna se samo tisto, kar se
dejansko premakne: če BTC zdrsne s 50 % na 53 %, plačaš od tistih treh
odstotnih točk, ne od celega portfelja.

**zamenjava** je enkratna menjava šestega mesta, kadar HYPE dobi prvi signal.

## Kaj je 0,30 % na stran

Privzeta provizija in zdrs skupaj. **To je predpostavka, ne meritev**, in edini
vhod v celoten izračun, ki ga ne poznamo.

Zato je v zavihku Stroški tabela, ki pokaže, koliko se izid premakne med
brezplačnim trgovanjem in 0,60 % na stran. Manjša ko je ta razlika, manj je
rezultat odvisen od nečesa, česar ne vemo.

## Objava na Streamlit Cloud

Glavna datoteka je `app.py` v korenu.

V naprednih nastavitvah **izberi Python 3.11 ali novejši**. Na starejšem
namestitev pandasa pade z napako, ki ne kaže očitno na verzijo.

Skrivnosti niso potrebne. Cene se predpomnijo za eno uro.

Prvo nalaganje traja dlje kot pri lean strani, ker prenese cene za osem
sredstev in na vsakem požene strategijo.

Če stran javi, da nima podatkov, požení `python scripts/preveri_vire.py`.

## Postavitev

```
app.py                  streamlit run app.py

model/                  strategija, ista kot v Diversitas Lean
  config.py             vsi parametri, z razlogom za vsakega
  strategy.py           avtomat stanj, pogoji, obrezovanje ogrevanja
  indicators.py         RSI, drseča povprečja, najvišje in najnižje
  warmup.py             obrezovanje ogrevanja
  costs.py              model stroškov
  data_source.py        pridobivanje cen

ui/
  dashboard.py          stran

scripts/
  preveri.py            račun in izris
  preveri_vire.py       dosegljivost virov cen
```

Mapa je zgrajena iz raziskovalnega repozitorija s skriptom
`testing/scripts/zgradi_sestavo.py`. Ta jo zna zgraditi znova, kadar se v
izvirniku kaj spremeni. Ročne spremembe v tej mapi bo pri naslednjem zagonu
prepisal.

## Kaj ta stran ni

Ni priporočilo za razporeditev. Raziskava, iz katere je nastala, je pokazala,
da košarica šestih **ne** premaga samega BTC prepričljivo, in da razpršitev med
kripto sredstvi prinese manj, kot bi človek pričakoval. Povprečna korelacija med
temi šestimi je 0,73, kar pomeni, da se vedejo skoraj kot eno sredstvo.

Stran obstaja zato, da to lahko vidiš sam, na svojih datumih in svojih utežeh.
