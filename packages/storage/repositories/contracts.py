"""Phase 3.2 — Rate Contract Repository."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from packages.domain.models import RateContract
from packages.storage.repositories.base import BaseRepository


class RateContractRepository(BaseRepository[RateContract]):
    """Repository for managing multi-tenant carrier rate contracts and tariffs."""

    def __init__(self, db: Session):
        super().__init__(RateContract, db)

    def get_by_contract_id(self, contract_id: uuid.UUID) -> Optional[RateContract]:
        """Look up a rate contract directly by primary key."""
        stmt = select(RateContract).where(RateContract.id == contract_id)
        return self.db.execute(stmt).scalars().first()

    def create_contract(
        self,
        organization_id: uuid.UUID,
        carrier_name: str,
        contract_number: str,
        customer_name: Optional[str] = None,
        lane_origin_state: Optional[str] = None,
        lane_origin_zip_prefix: Optional[str] = None,
        lane_dest_state: Optional[str] = None,
        lane_dest_zip_prefix: Optional[str] = None,
        effective_start_date: Optional[datetime] = None,
        effective_end_date: Optional[datetime] = None,
        rate_type: str = "flat",
        base_rate: float = 0.0,
        minimum_charge: float = 0.0,
        fuel_schedule: Optional[dict] = None,
        accessorial_schedule: Optional[dict] = None,
        class_rate_rules: Optional[dict] = None,
        is_active: bool = True,
    ) -> RateContract:
        """Create or update a carrier rate contract."""
        start_date = effective_start_date or datetime.now(timezone.utc)
        contract = RateContract(
            organization_id=organization_id,
            carrier_name=carrier_name,
            contract_number=contract_number,
            customer_name=customer_name,
            lane_origin_state=lane_origin_state,
            lane_origin_zip_prefix=lane_origin_zip_prefix,
            lane_dest_state=lane_dest_state,
            lane_dest_zip_prefix=lane_dest_zip_prefix,
            effective_start_date=start_date,
            effective_end_date=effective_end_date,
            rate_type=rate_type,
            base_rate=base_rate,
            minimum_charge=minimum_charge,
            fuel_schedule=fuel_schedule or {},
            accessorial_schedule=accessorial_schedule or {},
            class_rate_rules=class_rate_rules or {},
            is_active=is_active,
        )
        self.db.add(contract)
        self.db.commit()
        self.db.refresh(contract)
        return contract

    def find_matching_contract(
        self,
        organization_id: uuid.UUID,
        carrier_name: str,
        origin_state: Optional[str] = None,
        dest_state: Optional[str] = None,
        target_date: Optional[datetime] = None,
        destination_state: Optional[str] = None,
        **kwargs,
    ) -> Optional[RateContract]:
        """Find the most specific active contract for a carrier, lane, and effective date."""
        dest_state = dest_state or destination_state
        check_date = target_date or datetime.now(timezone.utc)

        stmt = select(RateContract).where(
            and_(
                RateContract.organization_id == organization_id,
                RateContract.carrier_name.ilike(f"%{carrier_name}%"),
                RateContract.is_active.is_(True),
                RateContract.effective_start_date <= check_date,
                or_(
                    RateContract.effective_end_date.is_(None),
                    RateContract.effective_end_date >= check_date,
                ),
            )
        )
        contracts = self.db.execute(stmt).scalars().all()
        if not contracts:
            return None

        # Prioritize exact lane match -> state wildcard match -> general contract
        for c in contracts:
            if (
                origin_state
                and dest_state
                and c.lane_origin_state
                and c.lane_dest_state
                and c.lane_origin_state.upper() == origin_state.upper()
                and c.lane_dest_state.upper() == dest_state.upper()
            ):
                return c

        for c in contracts:
            if not c.lane_origin_state and not c.lane_dest_state:
                return c

        return contracts[0]

    def list_contracts(
        self,
        organization_id: uuid.UUID,
        carrier_name: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> List[RateContract]:
        """List contracts for an organization with optional carrier and active filters."""
        stmt = select(RateContract).where(RateContract.organization_id == organization_id)
        if carrier_name:
            stmt = stmt.where(RateContract.carrier_name.ilike(f"%{carrier_name}%"))
        if is_active is not None:
            stmt = stmt.where(RateContract.is_active == is_active)
        return list(self.db.execute(stmt).scalars().all())
