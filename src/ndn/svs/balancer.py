#    @Author: Justin C Presley
#    @Author-Email: justincpresley@gmail.com
#    @Project: NDN State Vector Sync Protocol
#    @Source-Code: https://github.com/justincpresley/ndn-python-svs
#    @Pip-Library: https://pypi.org/project/ndn-svs/

# Basic Libraries
import asyncio as aio
import logging
from typing import Optional, Callable
# NDN Imports
from ndn.app import NDNApp
from ndn.encoding import Name, Component, InterestParam, BinaryStr, FormalName, SignaturePtrs
from ndn.types import InterestNack, InterestTimeout, InterestCanceled, ValidationFailure
# Custom Imports
from .security import SecurityOptions
from .state_table import StateTable
from .state_vector import StateVector
from .meta_data import MetaData

# Class Type: a ndn class
# Class Purpose:
#   manage sync interests that are sent out.
#   to hear about other sync interests
#   to find out about new data from other nodes.
class SVSyncBalancer:
    def __init__(self, app:NDNApp, groupPrefix:Name, nid:Name, table:StateTable, updateCallback:Callable, secOptions:SecurityOptions) -> None:
        self.app = app
        self.groupPrefix = groupPrefix
        self.nid = nid
        self.table = table
        self.updateCallback = updateCallback
        self.secOptions = secOptions
        self.busy = False
        self.balancePrefix = self.nid + self.groupPrefix + Name.from_str("/sync")
        self.app.route(self.balancePrefix, need_sig_ptrs=True)(self.onStateInterest)
        logging.info(f'SVSyncBalancer: started listening to {Name.to_str(self.balancePrefix)}')
    async def equalize(self, incoming_md:MetaData) -> None:
        self.busy = True
        if incoming_md.tseqno <= self.table.getMetaData().tseqno:
            self.busy = False
            return
        for i in range(incoming_md.nopcks):
            incoming_sv = await self.getStatePckValue(Name.from_str(bytes(incoming_md.source).decode()), i+1)
            if incoming_sv == None:
                break
            missingList = self.table.processStateVector(incoming_sv, oldData=True)
            if missingList:
                self.updateCallback(missingList)
            self.table.updateMetaData()
            if incoming_md.tseqno <= self.table.getMetaData().tseqno:
                break
        logging.info(f'SVSyncBalancer: nmeta {bytes(self.table.getMetaData().source).decode()} - {self.table.getMetaData().tseqno} total, {self.table.getMetaData().nopcks} pcks')
        logging.info(f'SVSyncBalancer: ntable {self.table.getCompleteStateVector().to_str()}')
        self.busy = False
    def onStateInterest(self, int_name:FormalName, int_param:InterestParam, _app_param:Optional[BinaryStr], sig_ptrs:SignaturePtrs) -> None:
        logging.info(f'SVSyncBalancer: received balance {Name.to_str(int_name)}')
        aio.get_event_loop().create_task(self.onStateInterestHelper(int_name, int_param, _app_param, sig_ptrs))
    async def onStateInterestHelper(self, int_name:FormalName, int_param:InterestParam, _app_param:Optional[BinaryStr], sig_ptrs:SignaturePtrs) -> None:
        sv = bytes(self.table.getPart(Component.to_number(int_name[-1])))
        logging.info(f'SVSyncBalancer: sending balance {sv}')
        self.app.put_data(int_name, content=sv, freshness_period=1000)
    async def getStatePckValue(self, source:Name, nopck:int) -> Optional[StateVector]:
        name = source + self.groupPrefix + Name.from_str("/sync") + [Component.from_number(nopck, Component.TYPE_SEQUENCE_NUM)]
        try:
            logging.info(f'SVSyncBalancer: balancing from {Name.to_str(name)}')
            data_name, meta_info, content = await self.app.express_interest(
                name, must_be_fresh=True, can_be_prefix=True, lifetime=1000)
            if bytes(content) == b'':
                return None
            return StateVector(bytes(content), self.sortByLatest)
        except (InterestNack, InterestTimeout, InterestCanceled, ValidationFailure) as e:
            return None
    def isBusy(self) -> bool:
        return self.busy