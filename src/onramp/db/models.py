"""
Model interface for OnRamp
"""
from tortoise.models import Model as TortoiseModel
from tortoise import fields as tortoise_fields

class Model(TortoiseModel):
    """
    Base model class - similar to Django's models.Model
    All OnRamp models should inherit from this
    """
    
    class Meta:
        abstract = True
    
    def __str__(self):
        return f"<{self.__class__.__name__}({getattr(self, 'pk', 'unsaved')})>"
    
    def __repr__(self):
        return self.__str__()
    
    @classmethod
    def objects(cls):
        """Django-style manager access"""
        return cls
    
    @classmethod
    async def create(cls, **kwargs):
        """Create and save a new instance"""
        instance = cls(**kwargs)
        await instance.save()
        return instance
    
    @classmethod
    async def get_or_create(cls, defaults=None, **kwargs):
        """Get an existing instance or create a new one"""
        try:
            instance = await cls.get(**kwargs)
            return instance, False
        except cls.DoesNotExist:
            create_kwargs = kwargs.copy()
            if defaults:
                create_kwargs.update(defaults)
            instance = await cls.create(**create_kwargs)
            return instance, True

# Field types - wrap Tortoise fields with Django-like API
class CharField(tortoise_fields.CharField):
    """Character field - equivalent to Django's CharField"""
    pass

class TextField(tortoise_fields.TextField):
    """Text field for longer content"""
    pass

class IntegerField(tortoise_fields.IntField):
    """Integer field"""
    pass

class BigIntegerField(tortoise_fields.BigIntField):
    """Big integer field"""
    pass

class SmallIntegerField(tortoise_fields.SmallIntField):
    """Small integer field"""
    pass

class FloatField(tortoise_fields.FloatField):
    """Float field"""
    pass

class DecimalField(tortoise_fields.DecimalField):
    """Decimal field for precise numbers"""
    pass

class BooleanField(tortoise_fields.BooleanField):
    """Boolean field"""
    pass

class DateTimeField(tortoise_fields.DatetimeField):
    """DateTime field"""
    pass

class DateField(tortoise_fields.DateField):
    """Date field"""
    pass

class TimeField(tortoise_fields.TimeField):
    """Time field"""
    pass

class JSONField(tortoise_fields.JSONField):
    """JSON field"""
    pass

class UUIDField(tortoise_fields.UUIDField):
    """UUID field"""
    pass

# Relationship fields
class ForeignKeyField(tortoise_fields.ForeignKeyField):
    """Foreign key relationship"""
    pass

class OneToOneField(tortoise_fields.OneToOneField):
    """One to one relationship"""
    pass

class ManyToManyField(tortoise_fields.ManyToManyField):
    """Many to many relationship"""
    pass

# Convenience aliases (Django compatibility)
AutoField = tortoise_fields.IntField  # Primary key auto field
EmailField = CharField  # Email is just a CharField with validation
URLField = CharField    # URL is just a CharField with validation

# Export all field types
__all__ = [
    'Model',
    'CharField', 'TextField', 'IntegerField', 'BigIntegerField', 
    'SmallIntegerField', 'FloatField', 'DecimalField', 'BooleanField',
    'DateTimeField', 'DateField', 'TimeField', 'JSONField', 'UUIDField',
    'ForeignKeyField', 'OneToOneField', 'ManyToManyField',
    'AutoField', 'EmailField', 'URLField'
]