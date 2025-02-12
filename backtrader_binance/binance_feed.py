from collections import deque

import pandas as pd

from backtrader.feed import DataBase
from backtrader.utils import date2num
from backtrader.utils.py3 import with_metaclass

from .binance_store import BinanceStore


class MetaBinanceData(DataBase.__class__):
    def __init__(cls, name, bases, dct):
        """Class has already been created ... register"""
        # Initialize the class
        super(MetaBinanceData, cls).__init__(name, bases, dct)

        # Register with the store
        BinanceStore.DataCls = cls


class BinanceData(with_metaclass(MetaBinanceData, DataBase)):
    params = (
        ('drop_newest', True),
    )
    
    # States for the Finite State Machine in _load
    _ST_LIVE, _ST_HISTORBACK, _ST_OVER = range(3)

    def __init__(self, **kwargs):
        self.store = BinanceStore(**kwargs)
        self._data = deque()

    def _load(self):
        if self._state == self._ST_OVER:
            return False
        
        elif self._state == self._ST_LIVE:
            return self._load_kline()
        elif self._state == self._ST_HISTORBACK:
            if self._load_kline():
                return True
            else:
                self._start_live()

    def _load_kline(self):
        try:
            kline = self._data.popleft()
        except IndexError:
            return None

        timestamp, open_, high, low, close, volume = kline

        self.lines.datetime[0] = date2num(timestamp)
        self.lines.open[0] = open_
        self.lines.high[0] = high
        self.lines.low[0] = low
        self.lines.close[0] = close
        self.lines.volume[0] = volume
        return True
    
    def _parser_dataframe(self, data):
        df = data.copy()
        df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        df['timestamp'] = df['timestamp'].values.astype(dtype='datetime64[ms]')
        df['open'] = df['open'].values.astype(float)
        df['high'] = df['high'].values.astype(float)
        df['low'] = df['low'].values.astype(float)
        df['close'] = df['close'].values.astype(float)
        df['volume'] = df['volume'].values.astype(float)
        # df.set_index('timestamp', inplace=True)
        return df
    
    def _parser_to_kline(self, timestamp, kline):
        df = pd.DataFrame([[timestamp, kline['o'], kline['h'],
                            kline['l'], kline['c'], kline['v']]])
        return self._parser_dataframe(df)
    
    def _process_kline_msg(self, msg):
        """https://binance-docs.github.io/apidocs/spot/en/#kline-candlestick-streams"""
        if msg['e'] == 'kline':
            if msg['k']['x']:  # Is closed
                kline = self._parser_to_kline(msg['k']['t'], msg['k'])
                self._data.extend(kline.values.tolist())
        elif msg['e'] == 'error':
            raise msg
    
    def _start_live(self):
        self._state = self._ST_LIVE
        self.put_notification(self.LIVE)
            
        self.store.binance_socket.start_kline_socket(
            self.symbol_info['symbol'],
            self._process_kline_msg,
            self.interval)
        self.store.start_socket()
        
    def haslivedata(self):
        return self._state == self._ST_LIVE and self._data

    def islive(self):
        return True
        
    def start(self):
        DataBase.start(self)

        self.interval = self.store.get_interval(self.p.timeframe, self.p.compression)
        if self.interval is None:
            self._state = self._ST_OVER
            self.put_notification(self.NOTSUPPORTED_TF)
            return
        
        self.symbol_info = self.store.get_symbol_info(self.p.dataname)
        if self.symbol_info is None:
            self._state = self._ST_OVER
            self.put_notification(self.NOTSUBSCRIBED)
            return

        if self.p.fromdate:
            self._state = self._ST_HISTORBACK
            self.put_notification(self.DELAYED)

            klines = self.store.binance.get_historical_klines(
                self.symbol_info['symbol'],
                self.interval,
                self.p.fromdate.strftime('%d %b %Y %H:%M:%S'))

            if self.p.drop_newest:
                klines.pop()
            
            df = pd.DataFrame(klines)
            df.drop(df.columns[[6, 7, 8, 9, 10, 11]], axis=1, inplace=True)  # Remove unnecessary columns
            df = self._parser_dataframe(df)
            self._data.extend(df.values.tolist())            
        else:
            self._start_live()
