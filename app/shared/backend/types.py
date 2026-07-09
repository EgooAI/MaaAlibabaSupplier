from pydantic import BaseModel


class CustomerExtraInfo(BaseModel):
    company_name: str = ""
    email: str = ""
    register_date: str = ""
    commercial_type: str = ""
    selling_platform: str = ""
    company_website: str = ""
    phone_number: str = ""
    landline_number: str = ""


class CustomerSpecialtyInfo(BaseModel):
    buyer_tag: list[str]
    buyer_specialty: list[str]
    interested_industries: list[str]


class CustomerD90Behavior(BaseModel):
    goods_explored: int = 0
    effective_inquiry: int = 0
    effective_rfq: int = 0
    login_days: int = 0
    bad_inquiry: int = 0
    blocked_by: int = 0


class CustomerInfo(BaseModel):
    user_name: str
    region_name: str
    uid: str

    extra_info: CustomerExtraInfo
    specialty_info: CustomerSpecialtyInfo
    d90_behavior: CustomerD90Behavior

