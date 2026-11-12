from django_filters import FilterSet, CharFilter, NumberFilter
from .models import CustomImage


class CustomImageFilter(FilterSet):
    year = NumberFilter(field_name='year')
    slug = CharFilter(field_name='slug')

    class Meta:
        model = CustomImage
        fields = ['year', 'slug']
