import email
import logging
import os
import re
import subprocess
import time
import warnings
import mailbox
from mailbox import mboxMessage
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd
import requests
import yaml
from bs4 import BeautifulSoup
from tqdm import tqdm

from config.config import CONFIG

from bigbang.bigbang_io import MessageIO, ListIO, ArchiveIO
from bigbang.ingress import (
    AbstractArchive,
    AbstractList,
    AbstractMessageParser,
)
from bigbang.ingress.utils import (
    get_website_content,
    set_website_preference_for_header,
    get_auth_session,
)
from bigbang.utils import (
    get_paths_to_files_in_directory,
    get_paths_to_dirs_in_directory,
)

filepath_auth = CONFIG.config_path + "authentication.yaml"
directory_project = str(Path(os.path.abspath(__file__)).parent.parent)
logging.basicConfig(
    filename=directory_project + "/w3c.scraping.log",
    filemode="w",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)
logger = logging.getLogger(__name__)


class W3CMessageParserWarning(BaseException):
    """Base class for Archive class specific exceptions"""

    pass


class W3CListWarning(BaseException):
    """Base class for Archive class specific exceptions"""

    pass


class W3CArchiveWarning(BaseException):
    """Base class for Archive class specific exceptions"""

    pass


class W3CMessageParser(AbstractMessageParser, email.parser.Parser):
    """
    This class handles the creation of an mailbox.mboxMessage object
    (using the from_*() methods) and its storage in various other file formats
    (using the to_*() methods) that can be saved on the local memory.

    Parameters
    ----------
    website : Set 'True' if messages are going to be scraped from websites,
        otherwise 'False' if read from local memory. This distinction needs to
        be made if missing messages should be added.
    url_pref : URL to the 'Preferences'/settings page.

    Methods
    -------
    from_url()
    _get_header_from_html()
    _get_body_from_html()

    Example
    -------
    To create a Email message parser object, use the following syntax:
    >>> msg_parser = W3CMessageParser(website=True)

    To obtain the Email message content and return it as `mboxMessage` object,
    you need to do the following:
    >>> msg = msg_parser.from_url(
    >>>     list_name="public-2018-permissions-ws",
    >>>     url="https://lists.w3.org/Archives/Public/public-2018-permissions-ws/2019May/0000.html",
    >>>     fields="total",
    >>> )
    """

    empty_header = {}

    def _get_header_from_html(self, soup: BeautifulSoup) -> Dict[str, str]:
        """
        Lexer for the message header.

        Parameters
        ----------
        soup : HTML code from which the Email header can be obtained.
        """
        header = {
            "message-ID": "#message-id",
            "Date": "#date",
            "To": "#to",
            "Cc": "#cc",
        }
        for key, value in header.items():
            try:
                header[key] = parse_dfn_header(
                    text_for_selector(soup, value)
                ).strip()
            except Exception:
                header[key] = ""
                continue
        header["Subject"] = text_for_selector(soup, "h1")

        from_text = parse_dfn_header(text_for_selector(soup, "#from"))
        from_name = from_text.split("<")[0].strip()
        from_address = text_for_selector(soup, "#from a")
        header["From"] = email.utils.formataddr(
            (from_name, email.header.Header(from_address).encode())
        )

        in_reply_to_pattern = re.compile('<!-- inreplyto="(.+?)"')
        match = in_reply_to_pattern.search(str(soup))
        if match:
            header["In-Reply-To"] = "<" + match.groups()[0] + ">"

        return header

    def _get_body_from_html(
        self, list_name: str, url: str, soup: BeautifulSoup
    ) -> Union[str, None]:
        """
        Lexer for the message body/payload.
        This methods assumes that the body is available in text/plain.

        Parameters
        ----------
        url : URL to the Email message.
        soup : HTML code from which the Email body can be obtained.
        """
        # TODO re-write using email.parser.Parser
        try:
            return text_for_selector(soup, "#body")
        except Exception:
            logger.info(f"The message body of {url} could not be loaded.")
            return None


class W3CList(AbstractList):
    """
    This class handles the scraping of a single W3C mailing list.

    Parameters
    ----------
    name : The name of the list (e.g. public-2018-permissions-ws, ...)
    source : Contains the information of the location of the mailing list.
        It can be either an URL where the list or a path to the file(s).
    msgs : List of mboxMessage objects

    Methods
    -------
    from_url()
    from_messages()
    from_mbox()
    get_messages_urls()
    get_period_urls()
    get_all_periods_and_their_urls()
    get_name_from_url()


    Example
    -------
    To scrape a W3C mailing list from an URL and store it in
    run-time memory, we do the following
    >>> mlist = W3CList.from_url(
    >>>     name="public-bigdata",
    >>>     url="https://lists.w3.org/Archives/Public/public-bigdata/",
    >>>     select={
    >>>         "years": 2015,
    >>>         "months": "August",
    >>>         "fields": "header",
    >>>     },
    >>> )

    To save it as *.mbox file we do the following
    >>> mlist.to_mbox(path_to_file)
    """

    @classmethod
    def from_url(
        cls,
        name: str,
        url: str,
        select: Optional[dict] = {"fields": "total"},
    ) -> "W3CList":
        """Docstring in `AbstractList`."""
        if "fields" not in list(select.keys()):
            select["fields"] = "total"
        msg_urls = cls.get_message_urls(name, url, select)
        return cls.from_messages(
            name,
            url,
            msg_urls,
            select["fields"],
        )

    @classmethod
    def from_messages(
        cls,
        name: str,
        url: str,
        messages: List[Union[str, mboxMessage]],
        fields: str = "total",
    ) -> "W3CList":
        """Docstring in `AbstractList`."""
        if not messages:
            msgs = []
        elif isinstance(messages[0], str):
            msg_parser = W3CMessageParser(
                website=True,
            )
            msgs = super().get_messages_from_urls(
                name, messages, msg_parser, fields
            )
        else:
            msgs = messages
        return cls(name, url, msgs)

    @classmethod
    def from_mbox(cls, name: str, filepath: str) -> "W3CList":
        """Docstring in `AbstractList`."""
        msgs = ListIO.from_mbox(filepath)
        return cls(name, filepath, msgs)

    @classmethod
    def get_message_urls(
        cls,
        name: str,
        url: str,
        select: Optional[dict] = None,
    ) -> List[str]:
        """Docstring in `AbstractList`."""

        def get_message_urls_from_period_url(name: str, url: str) -> List[str]:
            soup = get_website_content(url)
            a_tags = soup.select("div.messages-list a")
            if a_tags:
                a_tags = [
                    urljoin(url, a_tag.get("href"))
                    for a_tag in a_tags
                    if a_tag.get("href") is not None
                ]
            return a_tags

        msg_urls = []
        # run through periods
        for period_url in W3CList.get_period_urls(url, select):
            # run through messages within period
            for msg_url in get_message_urls_from_period_url(name, period_url):
                msg_urls.append(msg_url)
        return msg_urls

    @classmethod
    def get_period_urls(
        cls, url: str, select: Optional[dict] = None
    ) -> List[str]:
        """
        All messages within a certain period (e.g. January 2021).

        Parameters
        ----------
        url : URL to the W3C list.
        select : Selection criteria that can filter messages by:
            - content, i.e. header and/or body
            - period, i.e. written in a certain year and month
        """
        # create dictionary with key indicating period and values the url
        periods, urls_of_periods = cls.get_all_periods_and_their_urls(url)

        if any(
            period in list(select.keys()) for period in ["years", "months"]
        ):
            for key, value in select.items():
                if key == "years":
                    cond = lambda x: int(re.findall(r"\d{4}", x)[0])
                elif key == "months":
                    cond = lambda x: x.split(" ")[0]
                else:
                    continue

                periodquants = [cond(period) for period in periods]

                indices = W3CList.get_index_of_elements_in_selection(
                    periodquants,
                    urls_of_periods,
                    value,
                )

                periods = [periods[idx] for idx in indices]
                urls_of_periods = [urls_of_periods[idx] for idx in indices]
        return urls_of_periods

    @staticmethod
    def get_all_periods_and_their_urls(
        url: str,
    ) -> Tuple[List[str], List[str]]:
        """
        W3C groups messages into monthly time bundles. This method
        obtains all the URLs that lead to the messages of each time bundle.

        Returns
        -------
        Returns a tuple of two lists that look like:
        (['April 2017', 'January 2001', ...], ['ulr1', 'url2', ...])
        """
        # wait between loading messages, for politeness
        time.sleep(0.5)
        soup = get_website_content(url)
        periods = []
        urls_of_periods = []
        rows = soup.select("tbody tr")
        for row in rows:
            link = row.select("td:nth-of-type(1) a")
            if len(link) > 0:
                link = link[0]
            else:
                continue
            periods.append(link.text)
            urls_of_periods.append(url + link.get("href"))
        return periods, urls_of_periods

    @classmethod
    def get_messages_urls(cls, name: str, url: str) -> List[str]:
        """
        Parameters
        ----------
        name : Name of the W3C mailing list.
        url : URL to group of messages that are within the same period.

        Returns
        -------
        List of URLs from which `mboxMessage` can be initialized.
        """
        soup = get_website_content(url)
        if soup == "RequestException":
            return []
        else:
            a_tags = soup.select("div.messages-list a")
            if a_tags:
                a_tags = [
                    urljoin(url, a_tag.get("href"))
                    for a_tag in a_tags
                    if a_tag.get("href") is not None
                ]
            return a_tags

    @staticmethod
    def get_name_from_url(url: str) -> str:
        """Get name of mailing list."""
        return url.split("/")[-2]


class W3CArchive(AbstractArchive):
    """
    This class handles the scraping of W3C public mailing list archive in the
    hypermail format.

    Parameters
    ----------
    name : The name of the archive.
    url : The URL where the archive lives
    lists : A list containing the mailing lists as `W3CList` types

    Methods
    -------
    from_url()
    from_mbox()
    from_mailing_lists()
    get_lists()
    get_sections()
    to_dict()
    to_pandas_dataframe()
    to_mbox()

    Example
    -------
    To scrape a W3C mailing list archive from an URL and store it in
    run-time memory, we do the following
    >>> arch = W3CArchive.from_url(
    >>>     name="W3C",
    >>>     url_root="https://lists.w3.org/Archives/Public/",
    >>>     select={
    >>>         "years": 2015,
    >>>         "months": "November",
    >>>         "weeks": 4,
    >>>         "fields": "header",
    >>>     },
    >>>     instant_save=False,
    >>>     only_mlist_urls=False,
    >>> )

    To save it as *.mbox file we do the following
    >>> arch.to_mbox(path_to_directory)
    """

    @classmethod
    def from_url(
        cls,
        name: str,
        url_root: str,
        url_home: Optional[str] = None,
        select: Optional[dict] = None,
        instant_save: bool = True,
        only_mlist_urls: bool = True,
    ) -> "W3CArchive":
        """Docstring in `AbstractList`."""
        lists = cls.get_lists_from_url(
            select,
            url_root,
            url_home,
            instant_save,
            only_mlist_urls,
        )
        return cls.from_mailing_lists(
            name,
            url_root,
            lists,
            select,
            only_mlist_urls,
        )

    @classmethod
    def from_mailing_lists(
        cls,
        name: str,
        url_root: str,
        url_mailing_lists: Union[List[str], List[W3CList]],
        select: Optional[dict] = None,
        only_mlist_urls: bool = True,
        instant_save: Optional[bool] = True,
    ) -> "W3CArchive":
        """Docstring in `AbstractList`."""
        if isinstance(url_mailing_lists[0], str) and only_mlist_urls is False:
            lists = []
            for url in url_mailing_lists:
                mlist_name = W3CList.get_name_from_url(url)
                mlist = W3CList.from_url(
                    name=mlist_name,
                    url=url,
                    select=select,
                )
                if len(mlist) != 0:
                    if instant_save:
                        dir_out = CONFIG.mail_path + name
                        Path(dir_out).mkdir(parents=True, exist_ok=True)
                        mlist.to_mbox(dir_out=dir_out)
                    else:
                        logger.info(f"Recorded the list {mlist.name}.")
                        lists.append(mlist)
        else:
            lists = url_mailing_lists
        return cls(name, url_root, lists)

    @classmethod
    def from_mbox(
        cls,
        name: str,
        directorypath: str,
        filedsc: str = "*.mbox",
    ) -> "W3CArchive":
        """Docstring in `AbstractList`."""
        filepaths = get_paths_to_files_in_directory(directorypath, filedsc)
        lists = []
        for filepath in filepaths:
            name = filepath.split("/")[-1].split(".")[0]
            lists.append(W3CList.from_mbox(name, filepath))
        return cls(name, directorypath, lists)

    @staticmethod
    def get_lists_from_url(
        select: dict,
        url_root: str,
        url_home: Optional[str] = None,
        instant_save: bool = True,
        only_mlist_urls: bool = True,
    ) -> List[Union[W3CList, str]]:
        """Docstring in `AbstractList`."""
        archive = []
        if url_home is None:
            soup = get_website_content(url_root)
        else:
            soup = get_website_content(url_home)
        mlist_urls = [
            urljoin(url_root, h3_tag.select("a")[0].get("href"))
            for h3_tag in soup.select("h3")
            if h3_tag.select("a")
        ]
        mlist_urls = list(set(mlist_urls))  # remove duplicates

        if only_mlist_urls:
            # collect mailing-list urls
            for mlist_url in mlist_urls:
                name = W3CList.get_name_from_url(mlist_url)
                # check if mailing list contains messages in period
                _period_urls = W3CList.get_all_periods_and_their_urls(
                    mlist_url
                )[1]
                # check if mailing list is public
                if len(_period_urls) > 0:
                    loops = 0
                    for _period_url in _period_urls:
                        loops += 1
                        nr_msgs = len(
                            W3CList.get_messages_urls(
                                name=name, url=_period_url
                            )
                        )
                        if nr_msgs > 0:
                            archive.append(mlist_url)
                            break
        else:
            # collect mailing-list contents
            for mlist_url in mlist_urls:
                name = W3CList.get_name_from_url(mlist_url)
                mlist = W3CList.from_url(
                    name=name,
                    url=mlist_url,
                    select=select,
                )
                if len(mlist) != 0:
                    if instant_save:
                        dir_out = CONFIG.mail_path + name
                        Path(dir_out).mkdir(parents=True, exist_ok=True)
                        mlist.to_mbox(dir_out=CONFIG.mail_path)
                        archive.append(mlist.name)
                    else:
                        logger.info(f"Recorded the list {mlist.name}.")
                        archive.append(mlist)
        return archive


def text_for_selector(soup: BeautifulSoup, selector: str):
    """
    Filter out header or body field from website and return them as utf-8 string.
    """
    results = soup.select(selector)
    if results:
        result = results[0].get_text(strip=True)
    else:
        result = ""
        logging.debug("No matching text for selector %s", selector)

    return str(result)


def parse_dfn_header(header_text):
    header_texts = str(header_text).split(":", 1)
    if len(header_texts) == 2:
        return header_texts[1]
    else:
        logging.debug("Split failed on %s", header_text)
        return ""
