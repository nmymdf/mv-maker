# MV 製作工具 — 給 Claude 的專案說明

**先讀 https://github.com/nmymdf/READFIRST(README.md 與其他檔案):家族 App 的分工與規則。**
這份只寫這個 App 自己的事。

## 這是什麼
Windows 程式:選一首歌(Suno 做的)、匯入歌詞(LRC)、選照片和影片,自動排成 MV 並輸出 MP4(橫式 16:9 或直式 9:16)。可以自動從長影片挑好看的片段,也可以自己在表格裡改。給爸爸自己用。

## 分工
- Windows:爸爸與他的 Claude,本 repo `main` 分支。
- **iPhone 版:不需要**。

## 技術
- Python 3.12 + PySide6(介面)、OpenCV / Pillow / numpy(畫面合成)、ffmpeg(讀影片、編碼輸出)。
- `mvmaker/core.py`:資料、時間格式、歌詞檔、時間軸規劃(`build_plan`),`VERSION` 在這裡。
- `mvmaker/media.py`:照片動態、影片讀取、字幕、`Composer`(依時間產生畫面)、`export`。
- `mvmaker/autopick.py`:自動挑片段(清楚、亮度、晃動、人臉、換場景)。
- `mvmaker/ui_table.py`:Excel 式表格,整個 App 只用這一種輸入方式。
- `mvmaker/app.py`:主畫面。`mvmaker/smoke.py`:自動測試截圖。

## 爸爸的介面規則(一定要遵守)
- 繁體中文、大字(13pt 以上)、作者「作者:ArchieKUO」金色斜體 + 版本號在右上角。
- 輸入像 Excel:平常只顯示文字,正在輸入的那一格才出現下拉;點一下或 Tab 開始輸入;下拉要再點一下或按 ↓ 才打開,↑↓ 不換值;Enter 確定並跳下一格;滾輪絕對不能改值;空白輸入列在最下面,填完自動存;錯誤標紅不存。
- 數字靠右;批次動作先勾選再按按鈕;刪除前確認;不跳大的輸入視窗。
- 顏色跟著 Windows 深色/淺色模式。版面要對齊、置中、完整。
- 改畫面前先給爸爸看示意圖。

## 打包與交付
- push 到 `main` → GitHub Actions(`.github/workflows/build.yml`)在 Windows 上:測試(含實際輸出 MP4)→ PyInstaller 打包資料夾版 → 再用打包好的 exe 測一次 → 壓成 `MVMaker_v版本.zip` 放到 `exe` 分支(超過 95MB 會切成 `.part00`、`.part01`…,下載後用 `cat` 合併並對照 `SHA256.txt`)。
- 交付:把 zip 下載後直接傳到對話給爸爸,不要只給連結。檔名一定帶版本號。
- 本機測試:`QT_QPA_PLATFORM=offscreen MV_SMOKE=1 MV_DEMO=範例資料夾 MV_EXPORT_TEST=1 python main.py`,範例素材用 `python tools/make_demo.py 資料夾`。

## 目前狀態(每次改版更新)
- v0.1.0:第一版。選歌、匯入歌詞、邊聽邊標記、素材表(開始/結束/秒數/星號/原音)、自動挑片段、風格設定、預覽播放、輸出橫直式 MP4、專案自動存檔(存在歌曲旁邊 `歌名.MV專案.json`)。
- v0.1.1:txt 歌詞沒有時間 → 匯入時依字數先平均排上時間(灰色斜體 = 估計,標記或手改後變正式);播放器改成 `mvmaker/player.py`:Qt 播放器 2.5 秒沒動就自動改用 ffplay(環境變數 `MV_FFPLAY=1` 可強制);狀態列放大,警告黃底、錯誤紅底。
- 還沒做:風格範本(第二階段)、拖曳排序時間軸(第三階段)、換圖跟著節拍、濾鏡(第四階段)。
