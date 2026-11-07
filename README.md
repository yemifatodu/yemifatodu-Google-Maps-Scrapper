# Google Maps Scraper

A small tool that collects business details from Google Maps and lets you download them as a CSV or Excel file. It runs on your own computer with a simple web page, or from the command line.

## Features

- Web page with a search box, a live progress bar, a results table and a **Stop** button
- One-click **CSV** and **Excel (.xlsx)** downloads that open in Excel and Google Sheets
- Username and password login for the web page
- Skips places that Google lists more than once, and checks that the right place is loaded before reading its details, so rows are not mixed up
- Brings the scraper's Chrome window to the front so you can watch it work (Windows)
- Command-line mode for quick runs and for scripting

## What it collects

| Column | Description |
| --- | --- |
| `name` | Business name |
| `address` | Street address |
| `website` | Website address |
| `phone_number` | Phone number |
| `reviews_count` | Number of reviews |
| `reviews_average` | Average star rating |
| `place_type` | Category, for example Restaurant |
| `opens_at` | Opening or closing time shown on Maps |
| `introduction` | Short description, when Maps shows one |
| `store_shopping`, `in_store_pickup`, `store_delivery` | Yes or No |

## Requirements

- Windows PC (tested), Python 3.12
- Google Chrome installed

## Setup

Run these in PowerShell:

```powershell
git clone https://github.com/yemifatodu/yemifatodu-Google-Maps-Scrapper.git
cd yemifatodu-Google-Maps-Scrapper
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If you do not have Chrome, run `python -m playwright install chromium` as well.

## Set a login

Create a file named `login.txt` in the project folder with two lines: the username on the first line and the password on the second.

```
your-username
your-password
```

This file is listed in `.gitignore`, so it is never uploaded. Do not commit it or share it.

## Run the web page

```powershell
python maps_web.py
```

Then open http://127.0.0.1:5000, log in, type what to search for including the place (for example `restaurants in Ikeja Lagos`), choose how many results, and click **Start scraping**. A Chrome window opens and collects the places. When it finishes, use the download buttons.

Putting the tool on the internet is only safe with a strong password. When it is reachable from other devices it will not start unless the password has at least 12 characters and is not a common word.

## Run from the command line

```powershell
python maps_scraper.py -s "restaurants in Ikeja Lagos" -t 20
```

| Option | Meaning |
| --- | --- |
| `-s`, `--search` | What to search for, including the place |
| `-t`, `--total` | How many places to collect |
| `-o`, `--output` | CSV file to write (default `result.csv`) |
| `--append` | Add to the output file instead of replacing it |

## Notes

- Google changes how Maps is built from time to time. If a column comes back empty, the matching selector in `maps_scraper.py` probably needs updating.
- Keep runs small. Running many searches quickly can get you blocked by Google.
- Collecting data from Google Maps may be restricted by Google's terms of service. Use it responsibly and for lawful purposes only.

## License

MIT License. See the `LICENSE` file for the full terms.
