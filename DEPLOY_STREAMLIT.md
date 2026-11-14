# Deploy RFxDesk to Streamlit Community Cloud

The deployment package is prepared by `prepare_streamlit_deployment.py`. It contains the application, requirements, Linux OCR packages, theme, original synthetic demo files and saved demo extractions. It excludes `.env`, real secrets, uploaded comparisons and the local SQLite database.

## 1. Upload the prepared files to GitHub

1. Extract `RFxDesk_Streamlit_Deploy.zip`.
2. Create a GitHub repository named `rfxdesk`. A private repository is recommended for the assignment demo.
3. Choose **Add file → Upload files** and upload the extracted contents to the repository root. `app.py`, `requirements.txt` and `packages.txt` must appear at the root, with `.streamlit/`, `data/`, `vendor_files/`, `outputs/` and `demo_dataset_2/` alongside them.
4. Make sure the hidden `.streamlit/config.toml` is included. On macOS, Command+Shift+. shows hidden files in Finder. Alternatively create `.streamlit/config.toml` in GitHub and paste the prepared configuration.
5. Commit the upload. Do not upload the ZIP as the only repository file.

This browser workflow avoids installing Git/Xcode command-line tools on this Mac.

## 2. Create the Streamlit app

Open https://share.streamlit.io and sign in with GitHub. Select **Create app**, choose the repository and branch `main`, and set the main file path to **app.py**. Choose an available app subdomain if desired.

In **Advanced settings**, select **Python 3.11** and paste these TOML settings into **Secrets**:

```toml
OPENROUTER_API_KEY = "your_actual_openrouter_key"
OPENROUTER_MODEL = "google/gemini-2.5-flash"
```

Replace only the key placeholder; keep quotes. Do not paste the unquoted `.env` format into the TOML secrets field. Do not commit your real key to GitHub. Click **Save**, then **Deploy**.

Streamlit installs Python packages from `requirements.txt` and Linux packages from `packages.txt`. Deployment logs show errors if an installation or startup fails. The prepared source has been checked locally; an actual Cloud build has not yet been verified.

## 3. Check the live demo

- Demo quotes should show five vendors without an API call.
- Open a source file and verify the comparison and charts.
- Add the key and test RFx drafting, one uploaded quotation and analyst chat.
- Download the Excel comparison and verify formatting.
- Copy the resulting `https://...streamlit.app` URL into your submission.

## Access and persistence

Use **App settings → Sharing → Only specific people can view this app** for a private reviewer demo. Reviewers can then be added using Streamlit's sharing controls. A public link lets visitors use the configured OpenRouter account; this prototype has shared local history rather than per-user access control. Use synthetic documents only when publicly demonstrating it.

Community Cloud's working files are not a durable database. Saved uploads, quality documents, corrections, chat and SQLite history may be lost when the app is rebuilt/restarted. The committed synthetic demo data remains reproducible. Durable hosted history requires external storage/database integration.

## Common problems

- **No demo quotes:** verify that `outputs/V*.json`, `vendor_files/` and `data/rfx.json` are present in the repository. The package has a deployment-specific `.gitignore` that permits the five demo JSON files.
- **Missing API key/model:** use the two root-level TOML secrets shown above and restart/reboot the app if needed.
- **cv2 / libGL import error:** verify `packages.txt` was uploaded at the repository root.
- **Python package version errors:** select Python 3.11, compatible with the selected OCR package. Do not use Python 3.13+ with RapidOCR 1.4.4.
- **Old code:** confirm that the selected branch and `app.py` refer to the repository root you just updated.
