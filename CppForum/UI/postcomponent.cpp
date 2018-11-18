#include "postcomponent.h"
#include "User/user.h"
#include "Forum/post.h"
#include "Forum/forum.h"
#include "UI/postsarea.h"
#include "UI/commentsdialog.h"
#include <QDebug>


PostComponent::PostComponent(QWidget *parent) : QWidget(parent)
{

}

PostComponent::PostComponent(const Post &post, const int index, QWidget *parent)
    :QWidget(parent)
    ,index(index)
{
    this->setFixedSize(500, 150);

    vLayout = new QVBoxLayout(this);
    hLayout = new QHBoxLayout();

    //title
    title = new QLabel(this);
    title->setText(post.Title());
    vLayout->addWidget(title);

    //content
    content = new QTextBrowser(this);
    content->setText(post.Content());
    content->setMaximumHeight(200);
    vLayout->addWidget(content);

    //comment button
    comments = new QPushButton(this);
    commentsCount = post.Comments().size();
    comments->setText(QString::number(commentsCount) + tr(" Comments"));
    hLayout->addStretch();   //placeholder to make button align right
    hLayout->addWidget(comments);
    vLayout->addLayout(hLayout);

    //connect 'comment' button to commentsdialog
    connect(comments, &QPushButton::clicked,
            [=]{
        commentsDialog = new CommentsDialog(post, this);
        commentsDialog->show();
        connect(commentsDialog, &CommentsDialog::AddComment, [=]{
            comments->setText(QString::number(++commentsCount) + tr(" Comments"));
        });
    });

    //"Delete" button conditional appears
    //check if the requirement is met
    auto&& status = User::Get()->GetProfile().status;
    auto& curModerator = Forum::Get().GetCurBoard().ModeratorId();

    if( status == infrastructure::MODERATOR
            && User::Get()->Id() == curModerator
        || status == infrastructure::COMMON_USER
            && User::Get()->Id() == post.Poster()) {
        deletePost = new QPushButton(this);
        deletePost->setText(tr("Delete"));
        hLayout->addWidget(deletePost);

        //link delete button to another signal to pass parameter
        connect(deletePost, &QPushButton::clicked,
                [index = index, this]{
            if(Forum::Get().GetCurBoard().DeletePost(index)) {
                //send this signal need 'this' pointer
                emit DeletePostAtIndex(index);
            }
        });

        //reemit the signal to pass a paramater
        connect(this,
                &PostComponent::DeletePostAtIndex,
                qobject_cast<PostsArea*>(this->parent()),
                &PostsArea::OnDeletePost);
    }
}

