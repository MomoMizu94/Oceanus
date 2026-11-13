from weasyprint import HTML
from weasyprint.urls import FatalURLFetchingError, URLFetcher


from agent_core.report_document import build_report_document
from agent_core.report_limits import MAX_PDF_BYTES


class NoResourceFetcher(URLFetcher):
    def fetch(self, url, headers=None):
        raise FatalURLFetchingError("Report resource loading is disabled!")


def render_report_pdf(markdown: str, *, title: str = "Oceanus report") -> bytes:
    # Input validation and report building
    document = build_report_document(markdown, title=title)
    fetcher = NoResourceFetcher()
    renderer = HTML(string=document, url_fetcher=fetcher)
    pdf_bytes = renderer.write_pdf()

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise ValueError("Generated PDF exceeds the size limit!")

    return pdf_bytes
