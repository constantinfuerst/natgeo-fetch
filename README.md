# NatGeoFetch - National Geographic Archive PDF-Export on Steroids
Scrape Magazines from National Geographic Archive (requires subscription).
No watermarks, print-quality exports of any magazine found in the archive.
Tested only for recent issues and due to reliance on manual extraction of canvas
can sometimes be a bit unstable. Also supports automated sign-in, asking for
OTP if required, the recommended use however is to extract sign-in cookies
for your disney account from a browser in json format and add the file location
to the ini config. The script will also save cookies by itself.

TODO:
- improved install process by packaging for pypi
- tests for older magazines
