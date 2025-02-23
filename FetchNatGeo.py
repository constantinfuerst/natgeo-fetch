import os
import json
import base64
import argparse
import configparser

import playwright.sync_api as pw

from PIL import Image
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from dataclasses import dataclass
from typing import Tuple, Optional, List

from tqdm import tqdm

from multiprocessing import Pool


MONTH_MAP = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]


@dataclass
class Config:
    email: str
    password: str
    output_path: str
    cookie_path: str
    timeout: int
    vp_width: int
    vp_height: int

    def read(path: str = "config.ini") -> "Config":
        config = configparser.ConfigParser()
        config.read(path)

        return Config(
            email=config["credentials"]["email"],
            password=config["credentials"]["password"],
            output_path=config["storage"]["output-path"],
            cookie_path=config["storage"]["cookie-path"],
            timeout=int(config["timeouts"]["default"]),
            vp_width=int(config["viewport"]["width"]),
            vp_height=int(config["viewport"]["height"]),
        )


def _format_date(date_str: str) -> Tuple[int, int]:
    """
    Returns year and month given a date-string formatted as MM-YYYY.
    Month is returned starting with 0, so that if MM==01 (Janurary)
    the actual returned value at ret[1] is 0.
    """

    month, year = date_str.split("-")
    month = int(month) - 1
    year = int(year)

    assert month >= 0 and month < 12
    assert year > 1887 and year < 2100

    return year, month


def _signin_click_button(page: pw.Page, config: Config) -> bool:
    try:
        login_frame = page.frame_locator("iframe.ng-landing-wrapper.ng-scope")
        signin_button = login_frame.locator("a.loginGraybutton")
        signin_button.click(timeout=config.timeout)

        page.wait_for_load_state("domcontentloaded")

        return True

    except pw.TimeoutError:
        return False


def _signin_fill_email(page: pw.Page, config: Config) -> bool:
    try:
        login_entry_frame = page.frame_locator("iframe#oneid-iframe")
        email_input = login_entry_frame.locator("input[type='email']")
        email_input.fill(config.email, timeout=config.timeout)
        submit_button = login_entry_frame.locator("button[type='submit']")
        submit_button.click()

        page.wait_for_load_state("domcontentloaded")

        print(f"Filled E-Mail: {config.email}")

        return True

    except pw.TimeoutError:
        return False


def _signin_fill_password(page: pw.Page, config: Config):
    try:
        login_entry_frame = page.frame_locator("iframe#oneid-iframe")
        password_input = login_entry_frame.locator("input[type='password']")
        password_input.fill(config.password, timeout=config.timeout)
        submit_button = login_entry_frame.locator("button[type='submit']")
        submit_button.click()

        page.wait_for_load_state("domcontentloaded")

        print(f"Filled Password: {str(['*'] * len(config.password))}")

        return True

    except pw.TimeoutError:
        return False


def _signin_fill_otp(page: pw.Page, config: Config) -> bool:
    try:
        login_entry_frame = page.frame_locator("iframe#oneid-iframe")

        login_entry_frame.locator("#otp-code-input-0").first.fill(
            "0", timeout=config.timeout
        )

        otp_digits = input("Please provide 6-Digit OTP: ")

        for i in range(6):
            login_entry_frame.locator(f"#otp-code-input-{i}").fill(otp_digits[i])

        # TODO: fix this, currently does not click submit
        # maybe its fixed already by providing fixed range

        submit_button = login_entry_frame.locator("button[type='submit']")
        submit_button.click()

        page.wait_for_load_state("domcontentloaded")

        return True

    except pw.TimeoutError:
        return False


def _signin_cookies(page: pw.Page, config: Config):
    if os.path.isfile(config.cookie_path):
        with open(config.cookie_path, "r") as f:
            cookies = json.load(f)
            page.context.add_cookies(cookies)

    page.goto("https://archive.nationalgeographic.com")

    page.wait_for_load_state("domcontentloaded")

    signin_state = _signin_click_button(page, config)

    page.wait_for_timeout(config.timeout)

    try:
        page.wait_for_url(
            "https://archive.nationalgeographic.com/**", timeout=config.timeout
        )

        signin_state = False

    except pw.TimeoutError:
        pass

    if signin_state:
        signin_state = _signin_fill_email(page, config)

    if signin_state:
        signin_state = _signin_fill_password(page, config)

    if signin_state:
        signin_state = _signin_fill_otp(page, config)

    try:
        page.wait_for_url(
            "https://archive.nationalgeographic.com/**", timeout=config.timeout
        )
    except pw.TimeoutError:
        raise RuntimeError(
            """Something went wrong with sign-in. As a remedy, sign in to https://archive.nationalgeographic.com
            on your browser in a clean session (i.e. private mode). Then export the cookies of this session (all)
            using a cookie manager (i.e. 'Cookie Quick Manager' for Firefox) in JSON-Format. Place this file at
            the location pointed to by config.ini["storage"]["cookie-path"] and retry. If that does not solve
            the problem, there is a deeper issue. Maybe natgeo is not redirecting like it used to?"""
        )

    cookies = page.context.cookies()
    with open(config.cookie_path, "w") as f:
        json.dump(cookies, f)


import uuid


def _combine_canvas_sidebyside(
    img_left_data: bytes,
    img_right_data: bytes,
) -> bytes:
    """
    Takes two PNG-Images as bytes-Objects and puts them side-by-side
    returning the resulting image as a bytes-Object. Will internally
    convert the image to RGB-color.
    """

    img_left = Image.open(BytesIO(img_left_data))
    img_right = Image.open(BytesIO(img_right_data))

    new_width = img_left.width + img_right.width
    new_height = max(img_left.height, img_right.height)

    new_img = Image.new("RGB", (new_width, new_height))

    new_img.paste(img_left, (0, 0))
    new_img.paste(img_right, (img_left.width, 0))

    output = BytesIO()
    new_img.save(output, format="JPEG", quality=90)
    output = output.getvalue()

    return output


def _fetch_canvas_imagedata(
    page: pw.Page, canvas_id: int, retry: int = 10
) -> Optional[bytes]:
    for _ in range(retry):
        try:
            image_data = page.evaluate(
                f"""(
                    function() {{
                        let canvas = document.getElementById("{canvas_id}");
                        const ctx = canvas.getContext("2d");
                        ctx.globalCompositeOperation = "destination-over"; 
                        ctx.fillStyle = "white";
                        ctx.fillRect(0, 0, canvas.width, canvas.height);
                        return canvas.toDataURL("image/jpeg", 0.90).split(',')[1];
                    }})();
                """
            )

            image_data = base64.b64decode(image_data)

            return image_data

        except:
            page.wait_for_timeout(100)
    return None


def _download_article(page: pw.Page, config: Config, year: int, month: int):
    """
    Given a browser page/context which has the sign-in cookies for accessing
    https://archive.nationalgeographic.com this function downloads the magazine
    for given year (int, year as integer) and month (int, from range(0,12))
    and places it in the download directory from config.
    """

    article_url = f"https://archive.nationalgeographic.com/national-geographic/{str(year)}-{MONTH_MAP[month]}/flipbook/1/"

    page.goto(article_url)

    page.wait_for_load_state("networkidle")
    page.wait_for_load_state("domcontentloaded")

    fullscreen_button = page.locator("button[id='fullscreen']")
    fullscreen_button.click()

    n_pages = page.locator("div[class='spreaditem-div']").count()
    n_pages = 30

    pbar = tqdm(
        desc=f"Issue {month+1}/{year}: ",
        total=n_pages,
    )

    output_path = os.path.join(config.output_path, f"natgeo-{month+1}-{year}.pdf")

    c = canvas.Canvas(output_path)

    def __add_to_canvas(img: bytes):
        width, height = Image.open(BytesIO(img)).size
        c.setPageSize((width, height))
        c.drawImage(ImageReader(BytesIO(img)), 0, 0, width, height)
        c.showPage()

    __add_to_canvas(_fetch_canvas_imagedata(page, 1))

    pbar.update(1)

    for canvas_id in range(2, n_pages + 1, 2):
        page.wait_for_load_state("networkidle")
        next_button = page.locator("button[id='nextPage']")
        next_button.click()
        left_data = _fetch_canvas_imagedata(page, canvas_id)
        right_data = _fetch_canvas_imagedata(page, canvas_id + 1)
        combined = _combine_canvas_sidebyside(left_data, right_data)
        __add_to_canvas(combined)
        pbar.update(2)

    c.save()

    pbar.close()


def fetch_natgeo_range(config: Config, date_start: str, date_end: str):
    """
    Given two dates in format MM-YYYY and the config dataclass instance,
    this function downloads all national geographic magazines in the
    date range (inclusive on both sides) and places them in the output
    location pointed to by the config.
    To download just a single magazine set date_end == date_start.
    """

    year_start, month_start = _format_date(date_start)
    year_end, month_end = _format_date(date_end)

    current_year = year_start
    current_month = month_start

    with pw.sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        page = browser.new_page(
            viewport={"width": config.vp_width, "height": config.vp_height}
        )

        _signin_cookies(page, config)

        while True:
            _download_article(page, config, current_year, current_month)

            current_month = (current_month + 1) % 12
            current_year += current_month == 0

            if current_year >= year_end and current_month > month_end:
                break

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="""Download National Geographic issues from the archive.
        This script is mostly configured through an ini-File for which an
        example is distributed with the script. Sole dependency is playwrigth
        and the chromium browser."""
    )

    parser.add_argument(
        "--date-range",
        type=str,
        default="02-2025--02-2025",
        help="""Date range in MM-YYYY--MM-YYYY format (e.g., 01-2020--12-2024
        or 01-2025--01-2025 for a single download). Default is no range which
        automatically loads the latest entry in the archive.""",
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config.ini",
        help="Config file containing account info and output location.",
    )

    args = parser.parse_args()
    date_start, date_end = args.date_range.split("--")
    config = Config.read(args.config)

    fetch_natgeo_range(config, date_start, date_end)
