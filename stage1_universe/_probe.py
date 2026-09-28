"""One-off probe: connect, dump scanner parameters XML to disk. Not part of the pipeline."""
from allweather.stage1_universe.ibkr_client import IBKRUniverseClient
from allweather.config import DATA_DIR

def main():
    client = IBKRUniverseClient()
    client.connect()
    try:
        xml = client.get_scanner_parameters_xml()
        out = DATA_DIR / "scanner_parameters.xml"
        out.write_text(xml, encoding="utf-8")
        print(f"Wrote {len(xml)} chars to {out}")
    finally:
        client.disconnect()

if __name__ == "__main__":
    main()
