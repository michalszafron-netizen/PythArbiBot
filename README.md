# PythOracle MEV Bot

> **TL;DR**: Bot wykorzystujący opóźnienie pomiędzy off-chain ceną Pyth Network a ceną on-chain do zarabiania na likwidacjach pozycji na perpetual DEX-ach (GMX V2, Vela) na Arbitrum.

---

## 1. O co chodzi (Executive Summary)

Pyth Network to oracle cenowy używany przez setki protokołów DeFi. W przeciwieństwie do Chainlinka, Pyth działa w **modelu pull**: cena żyje off-chain w sieci Pythnet, a żeby trafiła on-chain, **ktoś musi wysłać podpisaną aktualizację**. Każdy może to zrobić.

Wnioski:
- W każdej chwili **on-chain cena Pyth** może być **przestarzała o sekundy lub minuty**
- **Off-chain cena Pyth** (z Hermes API) jest świeża co ~400ms
- Protokoły takie jak GMX V2 i Vela używają Pyth do likwidacji pozycji perpetual

**Nasza luka**: kiedy off-chain cena pokazuje, że pozycja użytkownika powinna być zlikwidowana, ale on-chain cena jeszcze tego nie wie — możemy w **jednej transakcji** (a) zaktualizować cenę Pyth, (b) zlikwidować pozycję, (c) zgarnąć liquidation reward.

Większość liquidator botów monitoruje wyłącznie on-chain stan. Przegapiają moment, gdy off-chain cena już mówi "likwiduj!" ale on-chain jeszcze nie. **Tutaj jest nasz edge**.

---

## 2. Mechanizm Pyth Pull Oracle (technicznie)

### Tradycyjny push oracle (Chainlink)
```
[Off-chain price feed] --automatic push--> [On-chain price] --read by--> [Protocol]
```
Cena aktualizuje się sama na podstawie progów (np. zmiana >0.5% lub co X minut). Aktualizacje płaci sam Chainlink.

### Pull oracle (Pyth)
```
[Off-chain Pythnet] --signed VAA--> [Hermes API]
                                          |
                              user/bot pulls VAA
                                          |
                                          v
                                  [On-chain Pyth Contract] --read by--> [Protocol]
```

VAA = Verifiable Action Approval. Podpisana wiadomość zawierająca cenę + timestamp. Aby zaktualizować cenę on-chain, musisz:
1. Pobrać VAA z Hermes (REST/WebSocket)
2. Wywołać `updatePriceFeeds(bytes[] updateData)` na kontrakcie Pyth na Arbitrum
3. Zapłacić ~$0.10-0.30 gas

### Kluczowy fakt
Większość protokołów wywołuje update + akcję **atomowo** w jednej transakcji. Ale **likwidator nie musi czekać** aż user/protocol wywoła update — **możemy sami zaktualizować cenę i wykonać likwidację** w tej samej tx.

---

## 3. Cele protokołów (target list)

| Protokół | Sieć | TVL | Konkurencja | Priorytet |
|---|---|---|---|---|
| **GMX V2** | Arbitrum | ~$500M | Średnia (3-5 botów) | 🥇 GŁÓWNY |
| **Vela Exchange** | Arbitrum | ~$30M | Niska (1-2 boty) | 🥈 |
| **Premia v3** | Arbitrum | ~$10M | Niska | 🥉 |
| **Synthetix v3** | Arbitrum | ~$50M | Średnia | Opcjonalny |
| **Mux Protocol** | Arbitrum | ~$20M | Niska | Opcjonalny |

**Strategia**: zaczynamy od GMX V2 (najwięcej wolumenu likwidacji), po stabilizacji dodajemy Vela/Premia (tam gdzie konkurencja praktycznie zerowa).

---

## 4. Architektura systemu

```
┌──────────────────────────────────────────────────────────────────┐
│                    PYTH HERMES STREAM                            │
│           (WebSocket — wss://hermes.pyth.network/ws)             │
│           Aktualizacje cen co ~400ms, free tier                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Off-chain prices
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              PRICE DELTA DETECTOR                                │
│   - Trzyma off-chain prices w pamięci (last & current)           │
│   - Co update porównuje z on-chain cenami (cache 1-3s)           │
│   - Sygnalizuje gdy delta > X%                                   │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Price moves of interest
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              POSITION INDEXER (per protocol)                     │
│   - Subskrybuje eventy GMX V2 (PositionIncrease/Decrease)        │
│   - Trzyma mapę: position_key → {collateral, size, leverage}     │
│   - Liczy liquidation_price dla każdej pozycji                   │
│   - Sortuje pozycje po liquidation_price (heap)                  │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Liquidatable positions
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│              EXECUTOR                                            │
│   - Buduje calldata: updatePriceFeeds + executeLiquidation       │
│   - Symuluje przez eth_call                                      │
│   - Wysyła tx przez RPC z high priority                          │
│   - Loguje wynik                                                 │
└──────────────────────────────────────────────────────────────────┘
```

### Komponenty (moduły Python)

| Moduł | Plik | Odpowiedzialność |
|---|---|---|
| Pyth feed | `pyth_monitor.py` | Połączenie z Hermes, parsing VAA |
| Position indexer | `gmx_indexer.py` | Tracking pozycji GMX V2 |
| HF calculator | `liquidation_calc.py` | Liczenie liquidation_price |
| Executor | `executor.py` | Build + send transaction |
| Config | `config.py` | Adresy kontraktów, RPC, klucze |
| Main loop | `main.py` | Orkiestracja |

---

## 5. Wymagania techniczne

### 5.1 Środowisko
- Python 3.10+ (async/await features)
- Git (do wersjonowania)
- Windows/Linux (testowane: Windows)

### 5.2 Biblioteki
```
web3>=6.20.0
websockets>=12.0
aiohttp>=3.9.0
python-dotenv>=1.0.0
eth-account>=0.11.0
eth-abi>=5.0.0
```

### 5.3 Dostępy zewnętrzne
- **Pyth Hermes API**: `https://hermes.pyth.network` (FREE, public)
- **Pyth WebSocket**: `wss://hermes.pyth.network/ws` (FREE)
- **Arbitrum RPC**: na MVP free tier Alchemy/QuickNode wystarczy. Docelowo dedicated node.
- **Portfel Ethereum**: nowy, z minimalnym ETH na gas ($10-20 starter)

### 5.4 Adresy kontraktów (Arbitrum One, chainId 42161)

#### Pyth
- **Pyth oracle**: `0xff1a0f4744e8582DF1aE09D5611b887B6a12925C`

#### GMX V2
- **DataStore**: `0xFD70de6b91282D8017aA4E741e9Ae325CAb992d8`
- **Reader**: `0xf60becbba223EEA9495Da3f606753867eC10d139`
- **OrderHandler**: `0xe68CAAACdf6439628DFD2fe624847602991A31eB`
- **LiquidationHandler**: `0xdAb9bA9e3a301CCb353f18B4C8542BA2149E4010`
- **ExchangeRouter**: `0x900173A66dbD345006C51fA35fA3aB760FcD843b`
- **EventEmitter**: `0xC8ee91A54287DB53897056e12D9819156D3822Fb`

#### Pyth Price Feed IDs (najważniejsze)
- ETH/USD: `0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace`
- BTC/USD: `0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43`
- ARB/USD: `0x3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5`

---

## 6. Plan rozwoju (Development Phases)

### Phase 1 — MVP-1: Pyth Feed Monitor ⏱️ 1-2 dni ✅
**Cel**: zobaczyć działający strumień cen z Hermes
- [x] Setup projektu, requirements, config template
- [x] WebSocket connection do Hermes
- [x] Subskrypcja 5 feedów (ETH, BTC, ARB, SOL, LINK)
- [x] Logowanie cen + latency (Hermes timestamp vs lokalny)
- [x] Statystyki (updates/sec, mean latency, p95)

**Sukces**: ✅ bot działa stabilnie, reconnect z backoff, plik: `pyth_monitor.py`

### Phase 2 — MVP-2: On-chain Pyth reader ⏱️ 2-3 dni ✅
**Cel**: czytać on-chain ceny Pyth i porównywać z off-chain
- [x] Web3 connection do Arbitrum
- [x] Czytanie `getPriceUnsafe` z kontraktu Pyth
- [x] Cache on-chain cen (refresh co 2s)
- [x] Logowanie delta = (off_chain - on_chain) / on_chain
- [x] Alert gdy |delta| ≥ 0.30% (DELTA_ALERT) lub staleness ≥ 5s (STALE)
- [x] Zapis do CSV (folder `data/`)

**Sukces**: ✅ Realne deltas wykryte — ARB +1.6%, SOL -0.8%, LINK -0.6%. Plik: `onchain_reader.py`

### Phase 3 — MVP-3: GMX V2 Position Indexer ⏱️ 1 tydzień 🔄
**Cel**: trzymać aktualną mapę pozycji GMX V2 i liczyć ich liquidation prices
- [x] Snapshot pozycji via subgraph (primary) + DataStore on-chain (fallback)
- [x] Kalkulator liquidation_price dla każdej pozycji
- [x] Distance % do likwidacji, sortowanie, zapis CSV
- [ ] Pobranie historycznych eventów PositionIncrease/Decrease
- [ ] Live event tracking (subskrypcja nowych eventów)
- [ ] Heap sortujący pozycje po liquidation_price (in-memory, real-time)

**Sukces**: Snapshot działa (`gmx_positions.py`). Brakuje: live event indexer → `gmx_indexer.py`

### Phase 4 — MVP-4: Simulated execution ⏱️ 1 tydzień
**Cel**: złożyć i zasymulować pełną transakcję likwidacji (BEZ wysyłania)
- [ ] Build calldata: updatePriceFeeds + executeLiquidation
- [ ] Symulacja przez `eth_call` na lokalnym forku (Anvil)
- [ ] Logowanie wyniku symulacji (sukces/błąd, profit estimate)
- [ ] Tracking ile likwidacji byśmy złapali (wins) vs nas ubiegnięto (losses)

**Sukces**: Bot przez 48h zbiera statystyki bez wysyłania tx. Wiemy ile byśmy zarobili teoretycznie.

### Phase 5 — Live execution z mikropozycjami ⏱️ 2 tygodnie
**Cel**: pierwsza realna likwidacja, nawet za $5
- [ ] Świeży portfel + minimum ETH na gas
- [ ] Filtr: tylko pozycje gdzie expected_profit > gas_cost × 3
- [ ] Real execution z monitoringiem
- [ ] Fail-safe: stop po 5 nieudanych z rzędu

**Sukces**: Przynajmniej 1 udana likwidacja zarejestrowana w logach

### Phase 6 — Optymalizacja i skalowanie ⏱️ ongoing
- Dodanie Vela Exchange, Premia v3
- Optymalizacja latency (dedicated node)
- Cascade modeling (gdy user A pada, kto następny)
- Analiza wygranych/przegranych vs konkurencja

---

## 7. Ryzyka i ograniczenia

### 7.1 Konkurencja
- Top boty na GMX V2: 3-5 znanych adresów, zarabiają $30-90k/mc każdy
- Mają lepszą latency (~10-30ms) niż my będziemy mieli (~50-150ms na free RPC)
- **Mitygacja**: szukamy okien gdy oni są offline, mniejsze pozycje, mniejsze protokoły

### 7.2 Techniczne
- **Gas spike**: na Arbitrum gas potrafi skoczyć 5x w czasie volatility — może zjeść profit
- **Failed tx**: każde nieudane ~$0.20 strat. Limit dziennej straty obowiązkowy.
- **RPC reliability**: free tier może rate-limitować w czasie volatility — wtedy gdy najbardziej potrzeba

### 7.3 Finansowe
- Kapitał startowy: $30-50 (gas + bufor)
- Maksymalna strata przed pivot: $200 (po wyczerpaniu — STOP, debug)
- Break-even: ~$50-100/mc (przy free RPC), ~$500/mc (przy paid RPC)

### 7.4 Bezpieczeństwo klucza prywatnego
- **NIGDY** nie commitować `.env`
- Klucz w pliku tylko z permissions 600
- Lepiej: keystore z hasłem (zaszyfrowane)
- Najlepiej: hardware wallet (przy większych pieniądzach)

---

## 8. Koszty

### Jednorazowe
| Pozycja | Koszt |
|---|---|
| Gas na deploy ewentualnego helper kontraktu | $5-15 |
| ETH starter na portfelu (gas) | $10-30 |

### Miesięczne (MVP)
| Pozycja | Koszt |
|---|---|
| Alchemy/QuickNode free tier | $0 |
| Pyth Hermes | $0 |
| Hosting (lokalnie) | $0 |
| Średni gas wydany na próby | $5-30 |

**Total miesięczny MVP: $5-30**

### Miesięczne (production, jeśli zarabia)
| Pozycja | Koszt |
|---|---|
| Dedicated RPC (Chainstack Trader) | $300-500 |
| VPS w EU (Hetzner FSN1) | $20-50 |
| Monitoring (Grafana Cloud free) | $0 |

**Total: ~$350-550** — opłaca się tylko jeśli zarabiamy >$1500/mc

---

## 9. Metryki sukcesu

### Per phase
- **Phase 1**: streaming 1h bez crashy, latency <500ms
- **Phase 2**: deltas wykryte, statystyki częstotliwości
- **Phase 3**: 100% pozycji GMX V2 zindeksowanych
- **Phase 4**: 48h bez fałszywych pozytywnych
- **Phase 5**: pierwsza udana likwidacja
- **Phase 6**: $200/mc consistent profit

### Long-term
- **Win rate** (% prób kończących sie sukcesem): cel >30%
- **PnL after gas**: dodatni przez 4 tygodnie z rzędu
- **Mean profit per liquidation**: >$5 (po gas)

---

## 10. Słowniczek

- **VAA** — Verifiable Action Approval. Podpisana wiadomość Pyth z ceną + timestampem.
- **Hermes** — publiczne API Pyth (REST + WebSocket) do pobierania VAA.
- **Pull oracle** — model gdzie cena nie jest pushowana automatycznie, tylko klient/użytkownik musi ją zaktualizować.
- **Liquidation price** — cena assetu przy której pozycja staje się likwidowalna.
- **Position key** — unikalny hash identyfikujący pozycję (account + market + collateral + isLong) na GMX V2.
- **Calldata** — zakodowane dane wywołania funkcji smart kontraktu.
- **eth_call** — symulacja wywołania bez wysyłania transakcji (free, off-chain).
- **Atomic execution** — wszystkie operacje w jednej transakcji: albo wszystkie się udadzą, albo wszystkie zostaną cofnięte.

---

## 11. Linki referencyjne

- Pyth Network docs: https://docs.pyth.network
- Pyth price feeds list: https://www.pyth.network/developers/price-feed-ids
- GMX V2 docs: https://docs.gmx.io
- GMX V2 contracts: https://github.com/gmx-io/gmx-synthetics
- Arbiscan: https://arbiscan.io

---

## 12. Status projektu

**Aktualny etap**: Phase 3 — MVP-3 (GMX V2 Position Indexer) 🔄
**Last updated**: 2026-05-07
