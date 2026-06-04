from agf.extract.ad_details import AdDetails


class FakeDetailsExtractor:
    """Async stand-in: maps external_ad_id -> AdDetails (master-plan §3.4)."""
    def __init__(self, details: dict):
        self._details = details            # {ad_archive_id(str): AdDetails}
        self.calls = []

    async def ad_details(self, ad_archive_id):
        self.calls.append(str(ad_archive_id))
        return self._details.get(str(ad_archive_id), AdDetails(None, False))
