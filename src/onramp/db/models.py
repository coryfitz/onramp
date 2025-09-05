"""
OnRamp DB - Model interface using Tortoise ORM
"""
from tortoise.models import Model as TortoiseModel, ModelMeta
from tortoise import fields as tortoise_fields
from typing import Any, Optional, Type, Dict, List

class OnRampModelMeta(ModelMeta):
    """Custom metaclass to handle automatic table name pluralization"""
    
    def __new__(cls, name, bases, namespace, **kwargs):
        # Only apply auto-pluralization to non-abstract models
        meta = namespace.get('Meta')
        is_abstract = meta and getattr(meta, 'abstract', False)
        
        # Don't auto-pluralize the base Model class or abstract models
        if name != 'Model' and not is_abstract:
            # Check if table name is already specified
            if not meta or not hasattr(meta, 'table'):
                # Create or update Meta class with pluralized table name
                plural_name = cls._pluralize(name)
                
                if not meta:
                    # Create new Meta class
                    class Meta:
                        table = plural_name.lower()
                    namespace['Meta'] = Meta
                else:
                    # Add table to existing Meta class
                    meta.table = plural_name.lower()
        
        return super().__new__(cls, name, bases, namespace, **kwargs)
    
    @staticmethod
    def _pluralize(word):
        """Simple pluralization rules for English words"""
        word = word.lower()
        
        # Special cases
        irregular = {
            'person': 'people',
            'child': 'children',
            'mouse': 'mice',
            'foot': 'feet',
            'tooth': 'teeth',
            'goose': 'geese',
            'man': 'men',
            'woman': 'women',
        }
        
        if word in irregular:
            return irregular[word]
        
        # Words ending in s, ss, sh, ch, x, z
        if word.endswith(('s', 'ss', 'sh', 'ch', 'x', 'z')):
            return word + 'es'
        
        # Words ending in consonant + y
        if word.endswith('y') and len(word) > 1 and word[-2] not in 'aeiou':
            return word[:-1] + 'ies'
        
        # Words ending in f or fe
        if word.endswith('f'):
            return word[:-1] + 'ves'
        elif word.endswith('fe'):
            return word[:-2] + 'ves'
        
        # Words ending in consonant + o
        if word.endswith('o') and len(word) > 1 and word[-2] not in 'aeiou':
            return word + 'es'
        
        # Default: just add 's'
        return word + 's'

class Model(TortoiseModel, metaclass=OnRampModelMeta):
    """
    Base model class
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
        """Manager access"""
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

# Field types - wrap Tortoise fields with API
class CharField(tortoise_fields.CharField):
    """Character field"""
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

# Relationship fields - these need special handling, so we'll use direct references
ForeignKeyField = tortoise_fields.ForeignKeyField
OneToOneField = tortoise_fields.OneToOneField
ManyToManyField = tortoise_fields.ManyToManyField

# Convenience aliases
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