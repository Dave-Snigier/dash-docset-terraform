#!/usr/bin/env python3
import argparse
import dataclasses
import re
import sqlite3
from os.path import join, relpath, dirname, isdir, basename
from typing import List, Optional
from urllib.parse import quote as url_quote

import markdown2
from bs4 import BeautifulSoup


@dataclasses.dataclass
class Args:
    input_file: str
    output_file: str
    docset_dir: str
    provider_dir: str
    flavor: str

    @property
    def index_file(self) -> str:
        return join(self.docset_dir, 'Contents', 'Resources', 'docSet.dsidx')

    @property
    def documents_dir(self) -> str:
        return join(self.docset_dir, 'Contents', 'Resources', 'Documents')

    @property
    def output_relative_stylesheet_file(self) -> str:
        return join(relpath(self.documents_dir, dirname(self.output_file)), 'style.css')

    @property
    def documents_relative_output_file(self) -> str:
        return relpath(self.output_file, self.documents_dir)

    @property
    def provider_relative_output_file(self) -> str:
        return relpath(dirname(self.output_file), self.provider_dir)

    @property
    def output_relative_provider_dir(self) -> str:
        return relpath(self.provider_dir, dirname(self.output_file))

    @staticmethod
    def parse() -> 'Args':
        args = argparse.ArgumentParser()
        args.add_argument('--in', dest='input_file', required=True)
        args.add_argument('--out', dest='output_file', required=True)
        args.add_argument('--docset', dest='docset_dir', required=True)
        args.add_argument('--provider', dest='provider_dir', required=True)
        args.add_argument('--flavor', dest='flavor', choices=['terraform', 'provider'], required=True)
        args = args.parse_args()
        return Args(
            input_file=args.input_file,
            output_file=args.output_file,
            docset_dir=args.docset_dir,
            provider_dir=args.provider_dir,
            flavor=args.flavor,
        )


def main():
    args = Args.parse()

    with open(args.input_file, 'r') as fp:
        input_data = fp.read()

    markdown_extras = [
        'fenced-code-blocks',
        'header-ids',
        'metadata',
        'tables',
    ]

    if args.flavor == 'provider':
        markdown_extras.append('code-friendly')

    body = markdown2.markdown(text=input_data, extras=markdown_extras)

    page = Page.from_markdown(body=body, args=args)

    html = render_full_page(args, page)
    html = add_section_anchors(html)
    html = update_hrefs(html, args)

    with open(args.output_file, 'w') as fp:
        fp.write(html)

    write_db(
        args.index_file,
        [
            Entry(
                name=page.index_title,
                type=page.index_entry_type,
                relative_path=args.documents_relative_output_file,
            ),
        ]
    )


@dataclasses.dataclass
class Page:
    title_metadata: str
    title_h1: str
    body: str
    flavor: str
    output_file: str

    @staticmethod
    def from_markdown(body: markdown2.UnicodeWithAttrs, args: Args) -> 'Page':
        soup = BeautifulSoup(str(body), 'html5lib')

        return Page(
            title_metadata=body.metadata.get('page_title', '').strip('" '),
            title_h1=soup.find_all('h1')[0].text if len(soup.find_all('h1')) > 0 else '',
            body=str(body),
            output_file=args.output_file,
            flavor=args.flavor,
        )

    @property
    def index_title(self) -> str:
        if self.is_data_source or self.is_resource:
            resource_name = derive_resource_name(self.title_metadata, self.title_h1)

            if not resource_name:
                raise RuntimeError('failed to derive resource name')

            return resource_name

        title = self.title_h1 or self.title_metadata or '???'

        if self.index_entry_type == 'Function':
            title = title.removesuffix(' Function')

        return title

    @property
    def index_entry_type(self) -> str:
        if self.title_metadata.endswith('Functions - Configuration Language'):
            return 'Function'
        elif self.is_data_source:
            return 'Directive'
        elif self.is_resource:
            return 'Resource'
        else:
            return 'Guide'

    @property
    def is_resource(self) -> bool:
        return self.flavor == 'provider' and basename(dirname(self.output_file)) == 'resources'

    @property
    def is_data_source(self) -> bool:
        return self.flavor == 'provider' and basename(dirname(self.output_file)) == 'data-sources'


def render_full_page(args: Args, page: Page) -> str:
    return f'''
        <html>
            <head>
                <title>{page.index_title}</title>
                <link rel="stylesheet" href="{args.output_relative_stylesheet_file}">
            </head>
            <body>
                {page.body}
            </body>
        </html>
    '''


def add_section_anchors(html: str) -> str:
    soup = BeautifulSoup(html, 'html5lib')

    for tag_name in ['h2', 'h3', 'h4', 'h5', 'h6']:
        for tag in soup.find_all(tag_name):
            anchor = soup.new_tag('a')
            anchor['name'] = '//apple_ref/cpp/Section/' + url_quote(tag.text)
            anchor['class'] = ['dashAnchor']
            tag.insert_before(anchor)

    return str(soup)


def update_hrefs(html: str, args: Args) -> str:
    soup = BeautifulSoup(html, 'html5lib')

    for a in soup.find_all('a'):
        if not a.get('href'):
            continue

        # No need to modify a relative path.
        if not a['href'].startswith('/'):
            continue

        # (re-construct the full href at the end of the loop)
        if '#' in a['href']:
            path, fragment = a['href'].split('#', 1)
            fragment = '#' + fragment
        else:
            path = a['href']
            fragment = ''

        path = path.strip('/')

        # Sometimes an absolute path such as /language/functions/chomp refers
        # to /language/functions/chomp.html, and sometimes it refers to
        # /langauge/functions/chomp/index.html. To determine which one it is,
        # check if, in the input file tree, the path refers to a folder.

        if isdir(join(dirname(args.input_file), args.output_relative_provider_dir, path)):
            path = path + '/index.html'
        else:
            path = path + '.html'

        # At this point, we need to convert an absolute path (such as
        # /language/functions/chomp) into a path relative to the current
        # file (e.g. if the current file is /language/functions/trimspace,
        # the relative path to chomp is ../../language/functions/chomp).

        path = join(relpath('.', args.provider_relative_output_file), path)

        # Reconstitute the full link.

        a['href'] = path + fragment

    return str(soup)


def derive_resource_name(metadata_page_title: str, page_h1: str) -> Optional[str]:
    metadata_page_title = metadata_page_title.strip()
    page_h1 = page_h1.strip()

    hardcoded = [
        (metadata_page_title, r'^External (?:Data Source|Resource)$', 'external'),
        (metadata_page_title, r'^HTTP Data Source$', 'http'),
    ]

    for text, pattern, resource_name in hardcoded:
        if re.match(pattern, text):
            return resource_name

    combinations = [
        (metadata_page_title, r'^[^:]+\: ([a-z0-9][a-z0-9_]+)(?: [Rr]esource| [Dd]ata [Ss]ource)?$'),
        (metadata_page_title, r'^([a-z0-9][a-z0-9_]+) (?:Resource|Data Source) \- terraform\-provider\-[a-z-]+$'),
        (metadata_page_title, r'^(?:Resource|Data Source) ([a-z0-9][a-z0-9_]+) \- terraform\-provider\-[a-z-]+$'),
        (page_h1, r'^(?:Resource|Data Source)\: ([a-z0-9][a-z0-9_]+)$'),
        (page_h1, r'^([a-z0-9][a-z0-9_]+)$'),
    ]

    for text, pattern in combinations:
        results = re.findall(pattern, text)
        if results:
            return results[0]

    return None


@dataclasses.dataclass
class Entry:
    name: str
    type: str
    relative_path: str


def write_db(db_path: str, index: List[Entry]):
    with sqlite3.connect(db_path, timeout=30.0) as conn:
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS searchIndex(id INTEGER PRIMARY KEY, name TEXT, type TEXT, path TEXT)')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS anchor ON searchIndex (name, type, path)')
        c.executemany(
            '''INSERT OR IGNORE INTO searchIndex(name, type, path) VALUES (?, ?, ?)''',
            [(entry.name, entry.type, entry.relative_path) for entry in index])
        conn.commit()


if __name__ == '__main__':
    main()
