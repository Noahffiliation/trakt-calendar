<h1 align="center">Trakt Calendar</h1>

## Table of Contents
- [About](#about)
- [Getting Started](#getting_started)
- [Usage](#usage)
- [Built Using](#built_using)
- [Authors](#authors)
- [Acknowledgments](#acknowledgement)

## About <a name = "about"></a>
Since Trakt upgraded everyone to V3, there are a lot of missing features that I rely on that are currently not avaialble. This project fetches everything in my watchlist and updates my calendar to mimic Trakts iCal feed functionality.

## Getting Started <a name = "getting_started"></a>
### Prerequisites
Before running the application, ensure you have Python installed and access to a Trakt API Client ID.
- Python 3.10+
- Google Cloud account

### Installing
A step by step series of examples that tell you how to get a development env running.
1. Clone the repository to your local machine:
    ```bash
    git clone https://github.com/Noahffiliation/trakt-calendar.git
    cd trakt-calendar
    ```

2. Create and activate a Python virtual environment:
    ```bash
    # Create virtual environment
    python -m venv .venv

    # Activate on Windows PowerShell:
    .\.venv\Scripts\Activate.ps1

    # Activate on macOS/Linux:
    source .venv/bin/activate
    ```

3. Install the required dependencies:
    ```bash
    pip install -r requirements.txt
    ```

4. Configure your environment variables in `.env`:
    ```bash
    cp .env.example .env
    ```

Edit `.env` to include your Trakt Client ID and username:
```env
TRAKT_CLIENT_ID=your_client_id_here
TRAKT_USERNAME=your_trakt_username
SYNC_GOOGLE=true
```

5. Set up Google Calendar Service Account key (optional for Google Sync):
    Place your `service_account.json` file in the project root directory and share your `Trakt Movies` and `Trakt TV Shows` Google Calendars with the Service Account email address with "Make changes to events" permission.

    Run the script to fetch calendar data, build `.ics` files, and sync with Google Calendar:
    ```bash
    python generate_ical.py --sync-google
    ```

### GitHub Actions Automated Sync Setup
To run the automated synchronization via GitHub Actions:
1. In your GitHub repository, go to **Settings** > **Secrets and variables** > **Actions**.
2. Add the following repository secrets:
   - `TRAKT_CLIENT_ID`: Your Trakt API Client ID.
   - `TRAKT_CLIENT_SECRET`: Your Trakt API Client Secret.
   - `TRAKT_ACCESS_TOKEN`: OAuth access token generated from `python auth.py`.
   - `TRAKT_REFRESH_TOKEN`: OAuth refresh token generated from `python auth.py`.
   - `SERVICE_ACCOUNT_JSON`: Contents of your `service_account.json` (optional for Google Calendar sync).
   - `SYNC_GOOGLE`: Set to `true` if syncing with Google Calendar.
   - `GOOGLE_SHARE_EMAIL`: Your primary Google account email to share created calendars with.

#### Automated OAuth Token Refresh
Trakt OAuth access tokens expire periodically. To allow GitHub Actions to automatically refresh tokens and persist the new secrets back to your repository:
1. Create a GitHub Personal Access Token (PAT) with repository secrets write permissions:
   - Go to **GitHub Settings** > **Developer Settings** > **Personal Access Tokens** > **Fine-grained tokens**.
   - Select your repository and grant **Repository permissions** > **Secrets: Read and write**.
2. Add the PAT as a repository secret named `GH_SECRETS_PAT`.
3. When Trakt automatically rotates the OAuth tokens during a sync run, the workflow will use `GH_SECRETS_PAT` to update `TRAKT_ACCESS_TOKEN` and `TRAKT_REFRESH_TOKEN` in your repository secrets automatically.

## Running the tests <a name = "tests"></a>
Explain how to run the automated tests for this system.

### Break down into end to end tests
End-to-end unit tests verify API data parsing, iCal file generation, filtering rules, and Google Calendar sync functionality using mocked responses.
```bash
python -m pytest tests/
```

### And coding style tests
Run test coverage checks to ensure code quality and coverage thresholds are met.
```bash
python -m pytest --cov=. tests/
```

## Usage <a name="usage"></a>
To generate updated iCal files locally:
```bash
python generate_ical.py
```

This will produce three `.ics` files in the current working directory:
- `trakt_movies.ics`: Contains release dates for movies in your watchlist.
- `trakt_shows.ics`: Contains air dates for TV episodes in progress and on your watchlist.
- `trakt_watchlist.ics`: Combined calendar containing both movies and TV show episodes.

To generate calendar files and trigger Google Calendar synchronization via Service Account:
```bash
python generate_ical.py --sync-google
```

## Built Using <a name = "built_using"></a>
- [Python](https://www.python.org/) - Programming Language
- [Trakt API](https://trakt.docs.apiary.io/) - Trakt.tv Media Data API
- [icalendar](https://github.com/collective/icalendar) - iCalendar Generator
- [Google Calendar API](https://developers.google.com/calendar) - Calendar Synchronization Service

## Authors <a name = "authors"></a>
- [@Noahffiliation](https://github.com/Noahffiliation) - Idea & Initial work

## Acknowledgements <a name = "acknowledgement"></a>
- Trakt V3 sucking
