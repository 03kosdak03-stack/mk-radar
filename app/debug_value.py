import json
import sys
from .service import UpdateService

if __name__ == '__main__':
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else 'AKSEN'
    print(json.dumps(UpdateService().value_ticker(ticker), ensure_ascii=False, indent=2, allow_nan=False))
