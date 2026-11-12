import re

from django.test import SimpleTestCase

from blog.templatetags.image_markup import optimize_content_images


class OptimizeContentImagesTests(SimpleTestCase):
    def test_first_image_is_eager_and_later_images_are_lazy(self):
        result = optimize_content_images('<p><img src="one.jpg"><img src="two.jpg"></p>')
        self.assertIn('src="one.jpg" class="content-image" loading="eager"', result)
        self.assertIn('src="two.jpg" class="content-image" loading="lazy"', result)

    def test_existing_classes_are_preserved_and_content_class_is_merged(self):
        result = optimize_content_images('<img src="one.jpg" class="hero featured">')
        self.assertIn('class="hero featured content-image"', result)

    def test_duplicate_real_attributes_are_collapsed(self):
        result = optimize_content_images(
            '<img src="one.jpg" class="hero" class="featured" loading="lazy" loading="auto">'
            '<img src="two.jpg" data-class="example" class=thumb loading=lazy loading=eager>'
        )
        tags = re.findall(r'<img\b[^>]*>', result)
        self.assertEqual(len(tags), 2)
        self.assertEqual(len(re.findall(r'(?<![\w:-])class\s*=', tags[0])), 1)
        self.assertEqual(len(re.findall(r'(?<![\w:-])loading\s*=', tags[0])), 1)
        self.assertIn('class="hero featured content-image"', tags[0])
        self.assertIn('loading="eager"', tags[0])
        self.assertEqual(len(re.findall(r'(?<![\w:-])class\s*=', tags[1])), 1)
        self.assertEqual(len(re.findall(r'(?<![\w:-])loading\s*=', tags[1])), 1)
        self.assertIn('data-class="example"', tags[1])
        self.assertIn('class="thumb content-image"', tags[1])
        self.assertIn('loading="lazy"', tags[1])

    def test_empty_content(self):
        self.assertEqual(optimize_content_images(None), "")
