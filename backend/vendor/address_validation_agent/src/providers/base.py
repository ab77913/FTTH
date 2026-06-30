from abc import ABC, abstractmethod
from src.models.schemas import CanonicalAddress, ProviderResult

class AddressProvider(ABC):
    @abstractmethod
    def validate(self, address: CanonicalAddress) -> ProviderResult:
        raise NotImplementedError
