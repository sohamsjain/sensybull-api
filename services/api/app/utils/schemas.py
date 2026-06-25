from marshmallow import Schema, fields, validate


class UserSchema(Schema):
    id = fields.Str(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    email = fields.Email(required=True)
    phone_number = fields.Str(validate=validate.Length(max=20))
    is_admin = fields.Bool(dump_only=True)
    email_verified = fields.Bool(dump_only=True)
    email_verified_at = fields.DateTime(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    watchlists = fields.List(fields.Nested('WatchlistSchema', exclude=('user', 'companies')), dump_only=True)


class UserRegistrationSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    email = fields.Email(required=True)
    password = fields.Str(required=True, validate=validate.Length(min=6))


class UserLoginSchema(Schema):
    email = fields.Email(required=True)
    password = fields.Str(required=True)


class EmailOnlySchema(Schema):
    email = fields.Email(required=True)


class TokenSchema(Schema):
    token = fields.Str(required=True, validate=validate.Length(min=16, max=128))


class ResetPasswordSchema(Schema):
    token = fields.Str(required=True, validate=validate.Length(min=16, max=128))
    new_password = fields.Str(required=True, validate=validate.Length(min=6))


class ChangePasswordSchema(Schema):
    current_password = fields.Str(required=True)
    new_password = fields.Str(required=True, validate=validate.Length(min=6))


class CompanySchema(Schema):
    id = fields.Str(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    ticker = fields.Str(validate=validate.Length(max=10))
    cik = fields.Str(validate=validate.Length(max=20))
    sic = fields.Str(validate=validate.Length(max=10))
    state_of_incorporation = fields.Str(validate=validate.Length(max=100))
    logo_url = fields.Str(dump_only=True, allow_none=True)
    created_at = fields.DateTime(dump_only=True)
    filings = fields.List(fields.Nested('FilingSchema', exclude=('company',)), dump_only=True)


class CompanyCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=200))
    ticker = fields.Str(validate=validate.Length(max=10))
    cik = fields.Str(validate=validate.Length(max=20))
    sic = fields.Str(validate=validate.Length(max=10))
    state_of_incorporation = fields.Str(validate=validate.Length(max=100))


class WatchlistSchema(Schema):
    id = fields.Str(dump_only=True)
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    description = fields.Str()
    user_id = fields.Str(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    user = fields.Nested('UserSchema', only=('id', 'name', 'email'), dump_only=True)
    companies = fields.List(fields.Nested('CompanySchema', exclude=('filings',)), dump_only=True)


class WatchlistCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=100))
    description = fields.Str()


class FilingSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(required=True)
    form_type = fields.Str(required=True, validate=validate.Length(min=1, max=20))
    filing_date = fields.Str(validate=validate.Length(max=20))
    period_of_report = fields.Str(validate=validate.Length(max=20))
    accession_number = fields.Str(validate=validate.Length(max=50))
    description = fields.Str()
    document_url = fields.Str(validate=validate.Length(max=500))
    created_at = fields.DateTime(dump_only=True)
    company = fields.Nested('CompanySchema', only=('id', 'name', 'ticker', 'cik'), dump_only=True)


class FilingCreateSchema(Schema):
    company_id = fields.Str(required=True)
    form_type = fields.Str(required=True, validate=validate.Length(min=1, max=20))
    filing_date = fields.Str(validate=validate.Length(max=20))
    period_of_report = fields.Str(validate=validate.Length(max=20))
    accession_number = fields.Str(validate=validate.Length(max=50))
    description = fields.Str()
    document_url = fields.Str(validate=validate.Length(max=500))


class AlertPreferenceSchema(Schema):
    id = fields.Str(dump_only=True)
    enabled = fields.Bool()
    max_tier = fields.Int(validate=validate.Range(min=1, max=3))
    channels = fields.Raw(attribute='channels_json')
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


class AlertPreferenceUpdateSchema(Schema):
    enabled = fields.Bool()
    max_tier = fields.Int(validate=validate.Range(min=1, max=3))
    channels = fields.Raw()


class NotificationSchema(Schema):
    id = fields.Str(dump_only=True)
    filing_event_id = fields.Str(dump_only=True)
    channel = fields.Str(dump_only=True)
    status = fields.Str(dump_only=True)
    error_message = fields.Str(dump_only=True)
    sent_at = fields.DateTime(dump_only=True)
    created_at = fields.DateTime(dump_only=True)
    filing_event = fields.Nested(
        'FilingEventSchema',
        only=('id', 'ticker', 'company_name', 'max_tier', 'event_types', 'received_at'),
        dump_only=True,
    )


class EventTypeSchema(Schema):
    id         = fields.Str(dump_only=True)
    type_name  = fields.Str(dump_only=True)
    attributes = fields.Raw(dump_only=True)
    created_at = fields.DateTime(dump_only=True)


class FilingEventSchema(Schema):
    id               = fields.Str(dump_only=True)
    edgar_id         = fields.Str(dump_only=True)
    signal_type      = fields.Str(dump_only=True)
    ticker           = fields.Str(dump_only=True)
    company_name     = fields.Str(dump_only=True)
    company_id       = fields.Str(dump_only=True)
    cik              = fields.Str(dump_only=True)
    filing_date      = fields.DateTime(dump_only=True)
    edgar_url        = fields.Str(dump_only=True)
    accession_number = fields.Str(dump_only=True)
    max_tier         = fields.Int(dump_only=True)
    items            = fields.Raw(dump_only=True)
    exhibits         = fields.Raw(dump_only=True)
    briefing         = fields.Raw(dump_only=True)
    event_types      = fields.List(fields.Str(), attribute="event_types_json", dump_only=True)
    event_type_details = fields.List(fields.Nested(EventTypeSchema), attribute="event_types", dump_only=True)
    analysis_status  = fields.Str(dump_only=True)
    analysis         = fields.Method("get_analysis", dump_only=True)
    received_at      = fields.DateTime(attribute="created_at", dump_only=True)

    def get_analysis(self, obj):
        analysis = getattr(obj, "analysis", None)
        return analysis.to_dict() if analysis else None
