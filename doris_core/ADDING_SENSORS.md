# Doris への新規センサ追加手順

NISAR (L/S-band RSLC) と BIOMASS (L1 SCS) を実装・実データ検証した経験をまとめたもの。
行番号は `doris_core/modernized/` の b3ee5a7 時点。

対象読者は、新しい衛星の SLC を Doris に読ませようとしている人。
**「ビルドは通るが数値が静かに壊れる」種類の失敗が多い**ので、
第 3 章の落とし穴と第 5 章のテストは飛ばさないこと。

---

## 1. 全体像

Doris 本体を新フォーマットのパーサで汚さず、**メタデータ抽出と SLC 展開を Python に外出しする**
方式を取っている。C++ 側はセンサ ID による分岐と `system()` 呼び出しだけを持つ。

```
入力ファイル                C++                          Python ヘルパ
-----------                ---                          ------------
M_IN_METHOD XXX   ──▶ sensor_id (readinput.cc)
                          │
                          ├─ readfiles ──▶ xxx_dump_header2doris.py ──▶ scratchres_xxx ──▶ master.res
                          │                                                                    │
                          │                         .res を読み戻して slcimage を作る ◀────────┘
                          │                         ここで sensor が再導出される (要注意)
                          │
                          └─ crop ──────▶ xxx_dump_data.py ──▶ *.raw (complex_real4)
                                                              + scratchres2raw
```

Python 側の責務は 2 本だけ。

| スクリプト | 入力 | 出力 |
|---|---|---|
| `xxx_dump_header2doris.py` | 製品のメタデータファイル | Doris `.res` 形式を **stdout** へ |
| `xxx_dump_data.py` | 製品の SLC 本体 + DBOW | `complex_real4` 生バイナリ |

---

## 2. 実装手順

### 2.1 センサ ID を定義する — `constants.hh`

```cpp
const int16 SLC_NISAR   = 91;   // :274 付近
const int16 SLC_NISAR_S = 92;
const int16 SLC_BIOMASS = 93;

const int16 SARPR_NISAR   = 19;
const int16 SARPR_BIOMASS = 20;
```

### 2.2 入力キーワードを追加する — `readinput.cc`

`M_IN_METHOD` (:1478) と `S_IN_METHOD` (:1666) の両方に分岐を足す。
**必ず 2 箇所**。片方だけだとスレーブで謎のエラーになる。

```cpp
} else if (!strcmp(keyword, "NISAR-S")) {
  m_readfilesinput.sensor_id = SLC_NISAR_S;
```

### 2.3 crop 関数を書く — `readdata.hh` / `readdata.cc`

`nisar_dump_data()` (:4480) か `biomass_dump_data()` (:4574) を雛形にする。
やることは `system()` でスクリプトを呼び、`scratchres2raw` に
`Data_output_format: complex_real4` と DBOW を書くだけ。

### 2.4 4 つの switch に case を足す — `processor.cc`

| 箇所 | 行 | switch 対象 |
|---|---|---|
| master readfiles | 276 | `input_m_readfiles.sensor_id` |
| slave readfiles | 1232 | `input_s_readfiles.sensor_id` |
| master crop | 741 | **`master.sensor`** |
| slave crop | 1711 | **`slave.sensor`** |

**readfiles と crop で switch している変数が違う**。これが 2.5 の話につながる。

### 2.5 ★ `.res` からのセンサ再導出を追加する — `slcimage.cc`

**ここを忘れると BIOMASS で実際に踏んだ障害が起きる。**

`slcimage.sensor` は既定値 `SLC_ERS` (:70) で、`.res` の
`Product type specifier:` 行 (:673) から `strstr` で再導出される。
新センサの分岐がないと **sensor は ERS のまま**になり、
crop が ERS 内部パスへ落ちて圧縮 TIFF を生 complex として読み、
`lp2xyz` 発散 → `choles: A not pos. def.` で異常終了する。

```cpp
pch = strstr(word, "BIOMASS");          // :854 付近
if (pch != nullptr) { sensor = SLC_BIOMASS; continue; }
pch = strstr(word, "NISAR-S");          // 部分一致なので長い方を先に
if (pch != nullptr) { sensor = SLC_NISAR_S; continue; }
pch = strstr(word, "NISAR");
if (pch != nullptr) { sensor = SLC_NISAR; continue; }
```

`SAR_PROCESSOR:` (:584) にも分岐を足すと "not identified, using VMP" と
ERS 公称値との比較警告が消える。

判定に使われるトークン位置は固定なので、Python 側の出力と必ず対応させること。

| `.res` の行 | パーサが見るトークン | 例 |
|---|---|---|
| `Product type specifier:` | キーワードから 3 個先 | `NISAR-S SSAR RSLC` → `NISAR-S` |
| `SAR_PROCESSOR:` | 1 個先 | `ISCE3 1.4.00` → `ISCE3` |

### 2.6 センサ固有の入力カードが要るなら

複数偏波・複数サブバンドなど「1 ファイルに複数の画像が入る」形式では
入力ファイルから選べるようにする。`M_IN_POL` / `M_IN_FREQ` が前例。

- `input_readfiles` と `input_crop` **両方**にフィールドを持たせ、
  **1 枚のカードで両方に書き込む**。readfiles と crop で食い違うと
  `.res` と実データが別物になる。
- 既定値は `setunspecified()`。未指定なら引数を付けない
  → 既存入力ファイルの挙動が変わらず、ゴールデンテストも通る。
- 値は `system()` に流れるので `checksafetoken()` (readinput.cc:115) で
  英数字 8 文字以内に制限する。シェルメタ文字はパース時点で弾く。
- どの値が有効かの判定は **Python 側に任せる**。製品が何を持っているかを
  知っているのはリーダなので、`polarisation HH not in ['RH', 'RV']` のような
  的確なメッセージが出せる。

---

## 3. 落とし穴

実際に踏んだもの、および踏みかけたもの。

### 3.1 ★ 軌道データポイント行は 4 列・127 文字未満

`orbit::initialize` (orbitbk.cc:128) はこう読む。

```cpp
infile.getline(dummyline, ONE27, '\n');          // ONE27 = 127
infile >> time(i,0) >> data_x >> data_y >> data_z;
```

- **読むのは t, x, y, z の 4 列だけ**。速度は多項式の微分で導出されるので
  ファイルから読まれない。
- **行が 127 文字を超えると `getline` が読み切れず**、残りが次の
  `>> time` に食われて軌道が完全に壊れる。BIOMASS で速度 3 列を付けて
  138 文字にしたところ、これを踏んだ。

```
 72163.937500 -4736406.1770000001416 3449157.2850000001490 3910486.9169999998994
└─ %.6f (real8。整数に丸めると OSV の端数 .9375 s ≒ 7 km の誤差)
```

なお `.res` の他の行は `4*ONE27`=508 バイトで読まれるため、
長いパス名 (250 文字超) は問題ない。**狭いのは軌道リーダだけ。**

### 3.2 ★ PRF は「出力グリッドのレート」であって送信 PRF ではない

BIOMASS・NISAR の両方で踏んだ。ハードウェア PRF には校正パルス等が
含まれ、画像のライン間隔とは一致しない。

| | ハードウェア PRF | 画像グリッド PRF |
|---|---|---|
| BIOMASS | `prfList/prf/value` = 1534.402 Hz | `1/azimuthTimeInterval` = **1018.800 Hz** |
| NISAR S | `nominalAcquisitionPRF` = 1883.239 Hz | `1/zeroDopplerTimeSpacing` = **1520.000 Hz** |

**検算**: `(最終ライン時刻 - 先頭ライン時刻) / (ライン数-1)` がライン間隔と
一致するか確かめる。BIOMASS では 1e-12 で一致した。
製品によっては `azimuthProcessingParameters/totalBandwidth` が
グリッド PRF と同値で、独立な裏付けになる。

### 3.3 レンジ時間が 1-way か 2-way かは画素間隔で判別する

`c/2 × (レンジ時間刻み)` が注記されている `rangePixelSpacing` と一致すれば
その時間刻みは 2-way。BIOMASS では 19.813869328 m 対 19.813869327800077 m で
厳密一致し、`RSR = 1/rangeTimeInterval` = 7.5652 MHz が確定した。

Doris 側の単位変換も確認しておく (slcimage.cc)。

| `.res` フィールド | 単位 | Doris 内部 |
|---|---|---|
| `Range_time_to_first_pixel (2way) (ms)` | ms, 2-way | `/2000` → s, 1-way (:274) |
| `Range_sampling_rate (computed, MHz)` | MHz | `×2e6` → `rsr2x` (:340) |
| `Total_range_band_width (MHz)` | MHz | `×1e6` |
| `Total_azimuth_band_width (Hz)` | Hz | そのまま |

### 3.4 Doppler centroid の基準を合わせる

Doris は `f_DC(tau) = a0 + a1·tau + a2·tau²`、**tau は第 1 画素からの
2-way レンジ時間 [s]** (slcimage.cc:1061 の使われ方から確定)。

製品側が別の基準時刻 t0 中心の多項式を持っている場合は二項展開で
再センタリングする (BIOMASS がこれ)。2D グリッドで与えられる場合は
中央方位行を tau で最小二乗当てはめする (NISAR がこれ)。

- `|a1| < 1.0` または `|a2| < 1.0` だと Doris が警告して 0 にする。
- 当てはめ残差は必ず確認する。NISAR S帯は 3e-5 Hz だったが
  L帯は 0.6 Hz あった (スワス幅が広く 2 次では足りない)。
- NISAR L帯は DC が方位方向にも 3〜10 Hz 変動する。Doris はレンジ多項式
  しか持てないので中央行で代表させるが、変動量は記録しておく。

### 3.5 軌道補間方式は弧長と点数で選び分ける

Doris は**全点にわたる大域多項式**か自然 3 次スプライン。
既定は点数 > 6 で degree 5 (orbitbk.cc:168, 230)。時間軸は
中央値を引いて 10 で割る正規化がされている (:1045) ので条件数は問題ない。

実測 (ISCE3 が記録する Hermite を基準に、画像時間帯で比較):

| 製品 | 軌道 | polyfit deg 5 | natural spline | 採用 |
|---|---|---|---|---|
| NISAR S | 64 点 / 630 s | 0.393 m | **0.011 m** | `ORB_INTERP SPLINE` |
| NISAR L | 18 点 / 170 s | **0.011 m** | 0.055 m | `ORB_INTERP POLYFIT 5` |
| BIOMASS | 100 点 / 99 s | **0.002 m** | — | 既定のまま |

**長い弧では多項式が破綻し、短い弧ではスプライン端点条件が効く。**
どちらが良いかは製品ごとに測ること。`polyfit()` は残差が 0.02 m を超えると
警告を出す (orbitbk.cc:1095) ので、これを tripwire に使う。

### 3.6 左視 (left-looking) に明示的な切替はない

`lp2xyz` (orbitbk.cc:482) は Doppler・レンジ・楕円体の連立を
Newton 法で解くだけで、左右の曖昧性は**初期値 `approxcentreoriginal`
のみ**で決まる。これは `.res` の `Scene_centre_latitude/longitude` から
作られる (slcimage.cc:1104)。

したがって **シーン中心を正しく書けば左視でもそのまま動く**。
逆に中心が嘘だと、左視データが静かに反対側にジオコードされる。
NISAR L/S はどちらも `lookDirection = Left` だったが問題なく通った。

`processor.cc` に軌道由来の中心と 50 km 以上ずれたら警告する検査があるので、
これが出ないことを確認する。

### 3.7 仕様書と実データは食い違う

BIOMASS は PDF から、NISAR は仕様から書いたが、**どちらも実データで
複数箇所外れていた**。

| 推測 | 実際 |
|---|---|
| orbit が `*_annot.xml` と同じ階層 | `annotation/navigation/*_orb.xml` |
| NISAR `swaths/frequencyA/zeroDopplerTime` | `swaths/zeroDopplerTime` (frequencyA の外) |
| NISAR `orbit/referenceEpoch` | **存在しない**。`orbit/time` の units 属性 |
| `rangeBandwidth` / `azimuthBandwidth` | `processedRangeBandwidth` / `processedAzimuthBandwidth` |
| BIOMASS `prfList/prf` のテキスト | `prfList/prf/value` (子要素) |
| 偏波は HH | NISAR S帯は compact pol で **RH/RV**、HH は無い |

**着手前に必ず実データの構造をダンプする。**

### 3.8 サブバンド・偏波の並び順を仮定しない

- NISAR L帯は frequencyA (1239.0 MHz) と frequencyB (**1293.5 MHz**) を持ち、
  **文字順と周波数順が逆**。`M_IN_FREQ LOW/HIGH` は中心周波数から解決している。
- frequencyB の `listOfPolarizations` は `['HV','HH']` で A とは逆順。
  「先頭を既定」にすると A と B で別偏波になる。
- 偏波名は製品の注記に従う。BIOMASS の COG バンド順は VRT の
  `PolarisationsSequence` (HH HV VH VV) で確認できる。

### 3.9 Python 側は 1 箇所に寄せる

header と crop の 2 本は C++ から**同じ引数**を渡される。解決ロジックが
食い違うと `.res` と実データが別物になる。`--freq` の解決は
`bin/nisar_common.py` に共有化した。

### 3.10 環境まわり

- **ビルドは `doris_core/modernized/` で行う。** `doris_core/` は改変していない
  元ソースで、ここからビルドすると新センサ対応が入らない。実際に一度踏んだ
  (`strings doris | grep -c BIOMASS` が 0 になる)。
- `processor.cc` は `system()` でスクリプトを**裸の名前**で呼ぶので
  `export PATH=<repo>/bin:$PATH` が必要。
- 依存ライブラリはシステム版で足りないことがある。BIOMASS の COG は
  `lerc_zstd` 圧縮で、システム GDAL 3.0.4 では "missing codec" になり
  rasterio 同梱の GDAL 3.9.2 でのみ読めた。**import できることと
  ファイルが開けることは別**なので、両方試して使える方を選ぶ実装にする。
- `cmd[512]` 等の固定長バッファは切り詰めを検査する。黙って途中までの
  コマンドを実行するのが最悪。

---

## 4. 入力ファイル作成時の注意

### 4.1 スレーブの切り出し窓はオフセットを考慮する

master と slave のフレーム長・開始時刻が違うと方位オフセットが大きくなる。

| | オフセット | 備考 |
|---|---|---|
| BIOMASS | -79 ライン | 同一フレーム長 |
| NISAR S | 33 ライン | ほぼ同一 |
| NISAR L | **1267 ライン** | 39520 対 41040 ライン、開始 1 s 差 |

NISAR L で ±500 ラインの余裕しか取らなかったところ、master 上部 20% に
slave データが無く、その領域のコヒーレンスが 0.50 → 0.002 に崩れた。

**手順**: まず `coarseorb` だけ流してオフセットを実測し、
`S_DBOW` をその分ずらしてから余裕を足す。

### 4.2 キーワードと値を機械的に検証する

`readinput.cc` に実在するか確認してから流す。列挙値も同様
(`COH_METHOD` は `REFPHASE_ONLY`/`INCLUDE_REFDEM` であって
`include_refpha` ではない、など)。

---

## 5. 必要なテスト

### 5.1 ゴールデンテスト (必須・毎回)

**`check_golden.sh` は doris を再実行せず、既存ファイルをハッシュするだけ。**
これだけでは新しいバイナリを検証したことにならない。実際に走らせる。

```bash
# 参照ハッシュを壊さないようスクラッチで実行する
# (run_golden.sh は golden_sha256.txt を上書きするので使わない)
mkdir -p /tmp/gt && cd /tmp/gt
G=<repo>/doris_core/modernized/golden
cp $G/doris_golden.txt $G/CPM_Data .
<repo>/doris_core/modernized/doris doris_golden.txt > log 2>&1
grep -vE "^#|^$" $G/golden_sha256.txt | sha256sum -c -
```

6/6 バイト一致が条件。**新センサのコードは既存センサの経路を通らない
はずだが、`slcimage.cc` のような共有ファイルを触ったら必ず確認する。**

### 5.2 メタデータの自己整合性 (実装中)

製品の注記だけを信じず、データ内部で閉じた検算をする。

```
(最終ライン時刻 - 先頭ライン時刻) / (ライン数 - 1)  ==  方位時間刻み
(最終レンジ時刻 - 先頭レンジ時刻) / (画素数 - 1)    ==  レンジ時間刻み
c/2 × レンジ時間刻み                                 ==  rangePixelSpacing
1/方位時間刻み                                       ==  azimuth totalBandwidth
```

### 5.3 ★ 軌道予測 vs 相関実測 (最も強い検証)

`coarseorb` が軌道から予測するオフセットと、`coarsecorr` が画像相関で
実測するオフセットを比較する。**PRF・RSR・レンジ時間・波長・軌道・
方位時刻のどれか 1 つでも間違っていれば大きく食い違う。**

| | 軌道予測 | 相関実測 | 差 |
|---|---|---|---|
| BIOMASS | -79, -47 | -76, -47 | 3 ライン, 0 画素 |
| NISAR L | 1267, -9 | 1266, -9 | **1 ライン**, 0 画素 |

数ライン以内で一致すれば、メタデータ一式が整合していると言ってよい。

### 5.4 切り出しデータの健全性

```python
NaN/Inf が 0、ゼロ画素が 0
位相の標準偏差 ≈ 1.8138   (一様分布 [-π,π] の理論値。ここから外れたら
                           複素数の組み立てを疑う)
```

### 5.5 偏波の物理検証 (多偏波センサ)

単スタティック SAR の相反則を使うと、バンド割り当ての誤りを検出できる。
BIOMASS 4 偏波で実測:

```
HV-VH の複素相関 = 0.90      ← 相反則。高いはず
同偏波×交差偏波   = 0.05-0.08 ← 無相関のはず
交差/同偏波の強度比 ≈ -8 dB
HV と VH の平均強度 0.21416 / 0.21228 ← ほぼ一致
```

band 3 を VV と取り違えていればこの構造は出ない。

### 5.6 干渉結果の解釈には基線長が要る

コヒーレンスの良し悪しは処理の正否と直結しない。**必ず `Bperp` と
高度アンビギュイティを見てから判断する。**

| | Bperp | 高度アンビギュイティ | coherence | 解釈 |
|---|---|---|---|---|
| BIOMASS | 1792 m | -70 m | 0.27 | 大基線。地形フリンジが密でコヒーレンス窓内で位相が回る。**処理は正常** |
| NISAR L | 35 m | -2209 m | 0.53 | 小基線。地形フリンジがほぼ無い |

コヒーレンスが低いときは、隣接画素の位相勾配集中度
`|<exp(i·Δφ)>|` をシャッフルした対照と比べるとフリンジの実在を確認できる
(BIOMASS: 0.20 対 対照 0.028)。ただし基線が小さくフリンジがほとんど
無い場合は対照自体が高く出るので、この指標は使えない。

高度アンビギュイティが小さい (数十 m) 場合、平坦地球のみの
`comprefpha`/`subtrrefpha` では不足で、DEM による
`comprefdem`/`subtrrefdem` が要る。

### 5.7 入力カードを足したとき

- 指定した値が実際に効いているか (別チャンネルで**異なるハッシュ**が出るか)
- 未指定時に従来と同一の結果になるか (後方互換)
- 不正値が明確なメッセージで拒否されるか
- **シェル注入が拒否されるか** — `M_IN_POL "RH;touch /tmp/pwned"` を流して
  ファイルが作られないことを確認する

---

## 6. チェックリスト

```
[ ] 実データの構造をダンプした (仕様書だけで書かない)
[ ] constants.hh       SLC_xxx / SARPR_xxx
[ ] readinput.cc       M_IN_METHOD と S_IN_METHOD の両方
[ ] readdata.hh/cc     xxx_dump_data()
[ ] processor.cc       4 つの switch (readfiles×2 は sensor_id、crop×2 は .sensor)
[ ] slcimage.cc        Product type specifier からのセンサ再導出  ★忘れやすい
[ ] slcimage.cc        SAR_PROCESSOR の識別
[ ] Python 2 本        header は stdout、data は complex_real4
[ ] 軌道行             4 列・127 文字未満・時刻は %.6f
[ ] PRF                出力グリッドのレート (送信 PRF ではない)
[ ] レンジ時間         2-way か 1-way か画素間隔で確認
[ ] f_DC               第 1 画素からの 2-way レンジ時間基準に再センタリング
[ ] シーン中心         実値 (左視の左右判定に使われる)
[ ] ORB_INTERP         弧長・点数に応じて polyfit / spline を実測で選択
[ ] ゴールデンテスト   doris を実走させて 6/6 バイト一致
[ ] 軌道予測 vs 相関   数ライン以内で一致
[ ] S_DBOW             実測オフセット分ずらしてから余裕を足す
```

---

## 7. 参考: 実装済みセンサ

| センサ | ID | 形式 | 特記事項 |
|---|---|---|---|
| NISAR L | `SLC_NISAR` 91 | HDF5 (ISCE3) | frequencyA/B、左視 |
| NISAR S | `SLC_NISAR_S` 92 | HDF5 (ISCE3) | compact pol (RH/RV)、左視 |
| BIOMASS | `SLC_BIOMASS` 93 | COG GeoTIFF + XML | 振幅/位相 2 ファイル、`lerc_zstd`、EOF 軌道 |

関連コミット:

```
401b55a feat: add NISAR and BIOMASS sensor support
a1422dc fix: identify BIOMASS and NISAR sensors when parsing .res
05ce69b feat: M_IN_POL / S_IN_POL to select the polarisation channel
e9cc80f feat: M_IN_FREQ / S_IN_FREQ to select the NISAR frequency sub-band
b3ee5a7 feat: accept LOW/HIGH in M_IN_FREQ, resolved by centre frequency
```
