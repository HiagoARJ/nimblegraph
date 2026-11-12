from django.views import View

from .models import Post


def post_list(request):
    return Post.objects.all()


class PostDetail(View):
    def get(self, request, pk):
        return Post.objects.get(pk=pk)
