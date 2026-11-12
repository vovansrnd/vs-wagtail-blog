from wagtail import blocks
from wagtail.images.blocks import ImageChooserBlock

class ProductPromoBlock(blocks.StructBlock):
    product = blocks.PageChooserBlock(target_model="blog.ProductPage")
    title = blocks.CharBlock(required=False)
    text = blocks.TextBlock(required=False)
    image = ImageChooserBlock(required=False)
    cta_text = blocks.CharBlock(required=False, default="Подробнее")

    class Meta:
        icon = "star"
        label = "Промо: продукт"
        template = "blog/blocks/product_promo.html"
